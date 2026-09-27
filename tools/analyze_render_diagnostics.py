#!/usr/bin/env python3
"""Identity-bound CPU/surface/GPU diagnostics; never add overlapping timings.

Input is a versioned JSON manifest, not an inferred association of filenames.
Only Python's standard library is needed. See docs/render-diagnostics.md.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import statistics
from collections import defaultdict
from pathlib import Path


TRACE = re.compile(r"(?P<task>\S+)-(?P<pid>\d+)\s+\[[^]]+\].*?\s"
                   r"(?P<time>\d+\.\d+):\s+(?P<label>[A-Za-z0-9_]+):(?P<tail>.*)")
FIELD = re.compile(r"\b(object|event|subtype|code|owner)=(-?\d+)")
SHA256 = re.compile(r"[a-fA-F0-9]{64}\Z")
STAGES = ("step", "draw", "present", "wait")
NOTES = [
    "CPU stages, GML events, uploads and surface work are inclusive; nested timings must not be added.",
    "GPU jobs overlap CPU work. GPU and CPU milliseconds are not an additive frame budget.",
    "Native publication rate is not GPU completion rate, command count, display refresh or input latency.",
    "Separate samples are independent passes, not per-frame correlated measurements.",
    "Missing instrumentation is unknown, not zero cost or zero fallbacks.",
]


def stats(values, unit="ms"):
    values = sorted(values)
    if not values:
        return {"status": "no_observations", "count": 0, "unit": unit}
    def percentile(fraction):
        return values[round((len(values) - 1) * fraction)]
    return {"status": "observed", "count": len(values), "unit": unit,
            "min": values[0], "median": statistics.median(values),
            "p95": percentile(.95), "p99": percentile(.99), "max": values[-1],
            "total": sum(values)}


def window(source):
    limits = source.get("window_ms", [0, None])
    if not isinstance(limits, list) or len(limits) != 2:
        raise ValueError("window_ms must be [start, end-or-null]")
    start, end = limits
    end = math.inf if end is None else end
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or not 0 <= start < end:
        raise ValueError("window_ms must satisfy 0 <= start < end")
    return start, end


def trace_events(path):
    events, warnings = [], []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = TRACE.search(line)
        if match:
            events.append({"time": float(match["time"]) * 1000,
                           "pid": match["pid"], "label": match["label"],
                           "tail": match["tail"]})
        if "LOST" in line or "MISSED EVENTS" in line:
            warnings.append("Trace reports lost events; paired attribution may be incomplete.")
        count = re.search(r"entries-in-buffer/entries-written:\s*(\d+)/(\d+)", line)
        if count and count[1] != count[2]:
            warnings.append("Trace buffer did not retain all written entries.")
    if any(b["time"] < a["time"] for a, b in zip(events, events[1:])):
        raise ValueError(f"{path.name}: trace timestamps are not ordered")
    return events, sorted(set(warnings))


def summarize_trace(path, source):
    events, warnings = trace_events(path)
    start, end = window(source)
    origin = events[0]["time"] if events else 0
    in_window = lambda time: start <= time - origin < end
    stacks = defaultdict(list)
    durations = defaultdict(list)
    counts = defaultdict(int)
    unmatched = 0
    kind = source["kind"]
    frames, active = [], {}
    step_pids = set()
    event_keys = {}
    for event in events:
        time, pid = event["time"], event["pid"]
        label = event["label"]
        if label.startswith("am2r_"):
            label = label[5:]
        returned = label.endswith("_ret")
        base = label[:-4] if returned else label
        if kind == "stages" and base not in STAGES:
            continue
        if kind == "events" and base != "resolved_event":
            continue
        if kind == "events" and not returned:
            fields = {key: int(value) for key, value in FIELD.findall(event["tail"])}
            names = ("object", "event", "subtype", "code", "owner")
            if not all(name in fields for name in names):
                # Keep a sentinel stack entry: a malformed inner event must not
                # consume the valid outer event's return.
                key = None
            else:
                key = "/".join(str(fields[name]) for name in names)
                event_keys[key] = fields
        else:
            key = base
        if not returned:
            stacks[(pid, base)].append((time, key))
            if key is not None and in_window(time):
                counts[key] += 1
        else:
            stack = stacks[(pid, base)]
            if not stack:
                if in_window(time):
                    unmatched += 1
            else:
                began, key = stack.pop()
                # Exclude calls crossing either window boundary instead of
                # assigning outside-window work to this route segment.
                if key is not None and in_window(began) and in_window(time):
                    durations[key].append(time - began)
        if kind != "stages":
            continue
        if label == "step":
            if in_window(time):
                step_pids.add(pid)
            previous = active.get(pid)
            if previous and in_window(previous["start"]) and in_window(time):
                previous["cycle"] = time - previous["start"]
                frames.append(previous)
            active[pid] = {"start": time}
        frame = active.get(pid)
        if frame is not None and not returned:
            frame.setdefault(label + "_start", time)
            if label == "wait":
                frame["work"] = time - frame["start"]
    unclosed = sum(1 for stack in stacks.values() for time, _ in stack if in_window(time))
    if unmatched or unclosed:
        warnings.append("Unmatched returns/entries can occur at capture boundaries; only fully paired calls are timed.")
    rows = []
    for key in sorted(set(counts) | set(durations)):
        row = {"name": key, "calls_entered": counts[key], "inclusive": stats(durations[key])}
        if key in event_keys:
            row["event"] = event_keys[key]
        rows.append(row)
    rows.sort(key=lambda row: row["inclusive"].get("total", 0), reverse=True)
    result = {"kind": kind, "clock": "tracefs; window relative to first parsed event",
              "paired_calls": rows, "unmatched_returns": unmatched,
              "unclosed_entries": unclosed, "warnings": warnings}
    if kind == "stages":
        cycles = [frame["cycle"] for frame in frames]
        work = [frame["work"] for frame in frames if "work" in frame]
        stage_data = {}
        for stage in STAGES:
            stage_data[stage] = stats(durations[stage])
            stage_data[stage]["basis"] = "paired entry/return, inclusive"
        result.update(stages=stage_data, work_before_wait=stats(work),
                      game_tick_intervals=stats(cycles),
                      game_tick_rate_hz=(1000 * len(cycles) / sum(cycles)) if cycles and sum(cycles) and len(step_pids) == 1 else None,
                      step_pids=sorted(step_pids),
                      game_tick_intervals_over_20ms=sum(value > 20 for value in cycles),
                      complete_cycles=len(frames))
        if len(step_pids) > 1:
            warnings.append("Multiple Step process IDs: duration distributions are pooled but no single game-tick rate is claimed.")
        # Entry-only captures still provide useful stage envelopes. Label them
        # separately; they include gaps and are not paired function durations.
        envelopes = {}
        for first, second in zip(STAGES, STAGES[1:]):
            values = [frame[second + "_start"] - frame[first + "_start"]
                      for frame in frames if first + "_start" in frame and second + "_start" in frame]
            envelopes[first + "_to_" + second] = stats(values)
        result["entry_envelopes"] = envelopes
    return result


def integer(value):
    return int(value, 0) if value.lower().startswith("0x") else int(value)


def counter_rate(rows, field):
    if len(rows) < 2:
        return {"status": "no_observations", "rate_hz": None}
    deltas = [(b[field] - a[field]) & 0xffffffff for a, b in zip(rows, rows[1:])]
    if any(delta > 0x7fffffff for delta in deltas):
        return {"status": "counter_reset_or_invalid", "rate_hz": None}
    elapsed = (rows[-1]["elapsed_ns"] - rows[0]["elapsed_ns"]) / 1e9
    return {"status": "observed", "increments": sum(deltas),
            "observed_span_s": elapsed, "rate_hz": sum(deltas) / elapsed,
            "largest_observed_increment": max(deltas)}


def summarize_gpu(path, source):
    clock_hz = source.get("gpu_clock_hz")
    if not isinstance(clock_hz, (int, float)) or not math.isfinite(clock_hz) or clock_hz <= 0:
        raise ValueError("GPU source requires positive gpu_clock_hz from its exact RBF")
    start, end = window(source)
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"elapsed_ns", "completed_seq", "cycles", "underflow", "commands",
                    "vblank_valid", "vblank", "scanout_frame", "native_frame"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("GPU CSV missing required columns")
        all_rows = [{key: integer(row[key]) for key in required} for row in reader]
    if any(b["elapsed_ns"] <= a["elapsed_ns"] for a, b in zip(all_rows, all_rows[1:])):
        raise ValueError("GPU timestamps must be strictly increasing")
    if any(value < 0 for row in all_rows for value in row.values()):
        raise ValueError("GPU CSV contains negative counters")
    if any(row[key] > 0xffffffff for row in all_rows for key in required - {"elapsed_ns"}):
        raise ValueError("GPU CSV counter exceeds uint32 range")
    if any(row["cycles"] > 0x1fffffff or row["vblank_valid"] not in (0, 1) or row["underflow"] not in (0, 1)
           for row in all_rows):
        raise ValueError("GPU CSV has invalid flags or cycle count")
    rows = [row for row in all_rows if start <= row["elapsed_ns"] / 1e6 < end]
    completed = counter_rate(rows, "completed_seq")
    publication_valid = bool(rows) and all(row["vblank_valid"] for row in rows)
    unknown = {"status": "unavailable_heartbeat", "rate_hz": None}
    return {"kind": "gpu", "clock": "sampler CLOCK_MONOTONIC_RAW; window relative to sampler start",
            "gpu_clock_hz": clock_hz, "samples": len(rows),
            "gpu_job_time": stats([row["cycles"] * 1000 / clock_hz for row in rows]),
            "commands_per_sampled_job": stats([row["commands"] for row in rows], "commands"),
            "completed_jobs": completed,
            "native_publication": counter_rate(rows, "native_frame") if publication_valid else unknown,
            "scanout_sequence": counter_rate(rows, "scanout_frame") if publication_valid else unknown,
            "vblank": counter_rate(rows, "vblank") if publication_valid else unknown,
            "sampled_underflow_flags": sum(row["underflow"] for row in rows) if rows else None,
            "warnings": ["Counters and scanout fields are asynchronous observations, not per-job pixel attribution.",
                         "Polling can miss jobs; zero sampled underflows is not proof of zero underflows outside observations."]}


def summarize_surfaces(path, source):
    """Aggregate optional instrumented surface CSV without inferring frame total.

    Stable interchange rows: elapsed_ns, frame, surface_id, operation, calls,
    pixels, elapsed_work_ns, uploaded_bytes. Optional values are blank, not zero.
    """
    start, end = window(source)
    groups = defaultdict(list)
    selected = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"elapsed_ns", "frame", "surface_id", "operation", "calls", "pixels",
                    "elapsed_work_ns", "uploaded_bytes"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("Surface CSV missing required columns")
        for row in reader:
            if int(row["elapsed_ns"]) < 0:
                raise ValueError("Surface CSV contains a negative elapsed timestamp")
            if start <= int(row["elapsed_ns"]) / 1e6 < end:
                groups[(row["surface_id"], row["operation"])].append(row)
                selected.append(row)
    rows = []
    for (surface, operation), records in sorted(groups.items()):
        item = {"surface_id": surface, "operation": operation, "records": len(records)}
        for field, factor, unit in (("calls", 1, "calls"), ("pixels", 1, "pixels"),
                                    ("elapsed_work_ns", 1e-6, "ms"), ("uploaded_bytes", 1, "bytes")):
            values = [int(row[field]) * factor for row in records if row[field] != ""]
            if any(value < 0 for value in values):
                raise ValueError("Surface CSV contains negative metrics")
            item[field] = stats(values, unit)
        rows.append(item)
    # Frame ids can rewind after a restore; elapsed timestamp plus frame is the
    # identity. Drop count is emitted on every row of a frame by the producer,
    # so take one maximum per frame rather than summing duplicated fields.
    frames = defaultdict(list)
    for row in selected:
        frames[(row["elapsed_ns"], row["frame"])].append(row)
    drop_counts = [max(int(row.get("dropped_scopes") or 0) for row in records)
                   for records in frames.values()]
    if any(count < 0 for count in drop_counts):
        raise ValueError("Surface CSV contains a negative dropped-scope count")
    drops_observed = bool(selected) and "dropped_scopes" in selected[0]
    warnings = ["Surface timings are inclusive and must not be added to CPU stage totals.",
                "The output_axis bucket includes output axis/triangle/clear dispatch and may include uploads; it is not exclusively pixel rasterization.",
                "Distributions describe emitted operation rows; absent operation rows are not silently filled with zero-cost frames."]
    if drops_observed and sum(drop_counts):
        warnings.append("Instrumentation dropped scopes; per-operation attribution is incomplete.")
    return {"kind": "surfaces", "clock": "instrumentation elapsed_ns; explicit producer origin",
            "groups": rows, "observed_frames": len(frames),
            "rooms": sorted({row["room"] for row in selected if row.get("room") != "" and "room" in row}),
            "input_masks": sorted({row["input_mask"] for row in selected if row.get("input_mask") != "" and "input_mask" in row}),
            "dropped_scopes": sum(drop_counts) if drops_observed else None,
            "warnings": warnings}


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def summarize_manifest(manifest, base_dir=Path(".")):
    if manifest.get("schema_version") != 1:
        raise ValueError("manifest schema_version must be 1")
    if not manifest.get("experiment_id"):
        raise ValueError("experiment_id is required")
    identities = manifest.get("identities", {})
    for key in ("rbf_sha256", "runner_sha256", "main_sha256", "state_sha256"):
        if key not in identities or (identities[key] is not None and not SHA256.fullmatch(str(identities[key]))):
            raise ValueError(f"identities.{key} must be SHA-256 or explicit null (unknown)")
    samples = manifest.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("manifest requires nonempty samples")
    if len(samples) > 1 and manifest.get("comparison") != "independent-runs":
        raise ValueError("Multiple samples require comparison: independent-runs; they are never automatically correlated")
    output = {"schema_version": 1, "experiment_id": manifest["experiment_id"],
              "identities": identities, "notes": NOTES.copy(), "samples": []}
    if any(value is None for value in identities.values()):
        output["notes"].append("One or more artifact/state identities are explicitly unknown; matched-build proof is incomplete.")
    seen_ids, seen_runs = set(), set()
    for sample in samples:
        for key in ("id", "run_id", "route", "instrumentation"):
            if not isinstance(sample.get(key), str) or not sample[key].strip():
                raise ValueError(f"sample requires nonempty {key}")
        if sample["id"] in seen_ids or sample["run_id"] in seen_runs:
            raise ValueError("Sample ids and run_ids must be unique; same-run sources belong in one sample")
        seen_ids.add(sample["id"])
        seen_runs.add(sample["run_id"])
        sources = sample.get("sources", [])
        if not sources:
            raise ValueError("sample requires sources")
        if len(sources) > 1 and sample.get("association") != "same-run":
            raise ValueError("Multiple sources require explicit association: same-run")
        result = {key: sample[key] for key in ("id", "run_id", "route", "instrumentation")}
        result.update({key: sample[key] for key in ("room", "association", "notes") if key in sample})
        result["sources"] = []
        coverage = {kind: "not_collected" for kind in ("stages", "functions", "events", "gpu", "surfaces")}
        for source in sources:
            kind = source.get("kind")
            path = base_dir / source["path"]
            if kind in ("stages", "functions", "events"):
                summary = summarize_trace(path, source)
            elif kind == "gpu":
                summary = summarize_gpu(path, source)
            elif kind == "surfaces":
                summary = summarize_surfaces(path, source)
            else:
                raise ValueError(f"Unknown source kind: {kind}")
            actual_hash = sha256_file(path)
            if source.get("sha256") and source["sha256"].lower() != actual_hash:
                raise ValueError(f"Input hash mismatch: {path.name}")
            summary.update(label=source.get("label", path.name), path=source["path"],
                           sha256=actual_hash, window_ms=source.get("window_ms", [0, None]))
            result["sources"].append(summary)
            coverage[kind] = "collected"
        result["coverage"] = coverage
        output["samples"].append(result)
    return output


def markdown(report):
    lines = [f"# Rendering diagnostics: {report['experiment_id']}", "", "## Identities", ""]
    lines += [f"- {key}: `{value or 'unknown'}`" for key, value in report["identities"].items()]
    lines += ["", "## Interpretation", ""] + [f"- {note}" for note in report["notes"]]
    def metric(name, value):
        if value["status"] != "observed":
            return f"- {name}: {value['status']}"
        return (f"- {name}: n={value['count']}, median {value['median']:.4f} {value['unit']}, "
                f"p95 {value['p95']:.4f}, max {value['max']:.4f}")
    for sample in report["samples"]:
        lines += ["", f"## {sample['id']} (run {sample['run_id']})", "", sample["route"], "",
                  f"Instrumentation: {sample['instrumentation']}", "",
                  "Coverage: " + ", ".join(f"{key}={value}" for key, value in sample["coverage"].items()) + "."]
        if sample.get("association"):
            lines += ["", f"Source association: {sample['association']} (no clock alignment inferred)."]
        if sample.get("notes"):
            lines += ["", "Sample note: " + sample["notes"]]
        for source in sample["sources"]:
            lines += ["", f"### {source['label']}", "", f"Input SHA-256: `{source['sha256']}`.", "",
                      f"Window: {source['window_ms']} ms. Clock: {source['clock']}.", ""]
            if source["kind"] == "stages":
                lines.append(metric("Work before wait (wall-time envelope)", source["work_before_wait"]))
                lines.append(metric("Game tick intervals", source["game_tick_intervals"]))
                lines += [metric(stage + " (inclusive)", value) for stage, value in source["stages"].items()]
                lines.append(f"- Game tick intervals over 20 ms: {source['game_tick_intervals_over_20ms']}")
            elif source["kind"] in ("functions", "events"):
                lines += [metric(row["name"] + " (inclusive)", row["inclusive"]) for row in source["paired_calls"][:20]]
            elif source["kind"] == "gpu":
                lines.append(metric("GPU job", source["gpu_job_time"]))
                for key in ("native_publication", "completed_jobs", "vblank"):
                    value = source[key]
                    lines.append(f"- {key}: " + (f"{value['rate_hz']:.6f} Hz" if value["rate_hz"] is not None else value["status"]))
                lines.append(f"- Sampled underflow flags: {source['sampled_underflow_flags']}")
            else:
                lines.append(f"- Observed frames: {source['observed_frames']}; dropped scopes: {source['dropped_scopes']}")
                for group in source["groups"]:
                    prefix = f"Surface {group['surface_id']} {group['operation']}"
                    lines.append(metric(prefix + " (inclusive)", group["elapsed_work_ns"]))
                    if group["operation"] == "upload" or group["uploaded_bytes"].get("total", 0):
                        lines.append(metric(prefix + " uploaded bytes", group["uploaded_bytes"]))
            lines += [f"- Note: {note}" for note in source["warnings"]]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
        report = summarize_manifest(manifest, args.manifest.parent)
        content = json.dumps(report, indent=2, allow_nan=False) + "\n" if args.format == "json" else markdown(report)
        if args.output:
            args.output.write_text(content, encoding="utf-8")
        else:
            print(content, end="")
    except (ValueError, KeyError, OSError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
