"""Synthetic, distributable regressions for identity-bound render diagnostics."""
from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "analyze_render_diagnostics.py"
SPEC = importlib.util.spec_from_file_location("render_diagnostics", SCRIPT)
DIAG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DIAG)


def event(time, label, pid=77, tail=""):
    return f"game-{pid} [000] ..... {time:.6f}: {label}: {tail}\n"


GPU_HEADER = "elapsed_ns,completed_seq,cycles,underflow,commands,vblank_valid,vblank,scanout_frame,native_frame\n"


class RenderDiagnosticsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.manifest = {
            "schema_version": 1, "experiment_id": "synthetic",
            "identities": {key: "a" * 64 for key in
                           ("rbf_sha256", "runner_sha256", "main_sha256", "state_sha256")},
            "samples": [{"id": "test", "run_id": "run-1", "route": "Synthetic stationary input",
                         "instrumentation": "synthetic", "sources": [{"kind": "stages", "path": "input.trace"}]}],
        }

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, contents):
        path = self.root / name
        path.write_text(contents, encoding="utf-8")
        return path

    def source(self, kind, name="input.trace", **extra):
        self.manifest["samples"][0]["sources"] = [{"kind": kind, "path": name, **extra}]

    def summarize(self):
        return DIAG.summarize_manifest(self.manifest, self.root)

    def test_complete_cpu_frames_and_missing_gpu(self):
        self.write("input.trace", "".join([
            event(1, "am2r_step"), event(1.01, "am2r_step_ret"),
            event(1.011, "am2r_draw"), event(1.013, "am2r_draw_ret"),
            event(1.014, "am2r_present"), event(1.015, "am2r_present_ret"),
            event(1.016, "am2r_wait"), event(1.032, "am2r_wait_ret"),
            event(1.033, "am2r_step")]))
        report = self.summarize()
        sample = report["samples"][0]
        source = sample["sources"][0]
        self.assertAlmostEqual(source["work_before_wait"]["median"], 16)
        self.assertAlmostEqual(source["stages"]["step"]["median"], 10)
        self.assertAlmostEqual(source["game_tick_rate_hz"], 1000 / 33)
        self.assertEqual(source["game_tick_intervals_over_20ms"], 1)
        self.assertEqual(sample["coverage"]["gpu"], "not_collected")
        self.assertNotIn("total_frame_work", source)
        self.assertIn("not an additive frame budget", DIAG.markdown(report))

    def test_entry_only_envelope_is_not_function_duration(self):
        self.write("input.trace", event(1, "am2r_step") + event(1.010, "am2r_draw") +
                   event(1.013, "am2r_present") + event(1.014, "am2r_wait") + event(1.017, "am2r_step"))
        source = self.summarize()["samples"][0]["sources"][0]
        self.assertEqual(source["stages"]["step"]["status"], "no_observations")
        self.assertAlmostEqual(source["entry_envelopes"]["step_to_draw"]["median"], 10)

    def test_nested_events_and_pid_isolation(self):
        self.source("events")
        outer = "object=727 event=3 subtype=0 code=2983 owner=727"
        inner = "object=727 event=7 subtype=11 code=2985 owner=727"
        self.write("input.trace", event(1, "resolved_event", tail=outer) +
                   event(1.001, "resolved_event", tail=inner) +
                   event(1.002, "resolved_event_ret", pid=88) +
                   event(1.008, "resolved_event_ret") + event(1.009, "resolved_event_ret"))
        source = self.summarize()["samples"][0]["sources"][0]
        timings = {row["event"]["code"]: row["inclusive"]["median"] for row in source["paired_calls"]}
        self.assertAlmostEqual(timings[2983], 9)
        self.assertAlmostEqual(timings[2985], 7)
        self.assertEqual(source["unmatched_returns"], 1)
        self.assertNotIn("exclusive_total", source)

    def test_boundary_crossing_calls_are_excluded(self):
        self.source("functions", window_ms=[2, 10])
        self.write("input.trace", event(1, "am2r_target") + event(1.005, "am2r_target_ret") +
                   event(1.006, "am2r_target") + event(1.008, "am2r_target_ret") +
                   event(1.009, "am2r_target") + event(1.012, "am2r_target_ret"))
        source = self.summarize()["samples"][0]["sources"][0]
        target = source["paired_calls"][0]
        self.assertEqual(target["calls_entered"], 2)
        self.assertEqual(target["inclusive"]["count"], 1)
        self.assertAlmostEqual(target["inclusive"]["median"], 2)

    def test_gpu_native_publication_is_not_job_rate(self):
        self.source("gpu", "input.csv", gpu_clock_hz=88000000)
        self.write("input.csv", GPU_HEADER +
                   "0,10,880000,0,10,1,100,200,200\n" +
                   "1000000000,70,880000,0,10,1,160,230,230\n")
        source = self.summarize()["samples"][0]["sources"][0]
        self.assertEqual(source["native_publication"]["rate_hz"], 30)
        self.assertEqual(source["completed_jobs"]["rate_hz"], 60)
        self.assertEqual(source["vblank"]["rate_hz"], 60)
        self.assertEqual(source["gpu_job_time"]["median"], 10)
        self.assertEqual(source["sampled_underflow_flags"], 0)

    def test_gpu_wrap_and_reset(self):
        rows = [{"elapsed_ns": 0, "counter": 0xfffffffe}, {"elapsed_ns": 1000000000, "counter": 1}]
        self.assertEqual(DIAG.counter_rate(rows, "counter")["increments"], 3)
        rows[0]["counter"] = 50
        self.assertEqual(DIAG.counter_rate(rows, "counter")["status"], "counter_reset_or_invalid")

    def test_gpu_invalid_heartbeat_is_unknown(self):
        self.source("gpu", "input.csv", gpu_clock_hz=88000000)
        self.write("input.csv", GPU_HEADER +
                   "0,10,880000,0,10,0,100,200,200\n" +
                   "1000000000,70,880000,0,10,0,160,230,230\n")
        source = self.summarize()["samples"][0]["sources"][0]
        self.assertIsNone(source["native_publication"]["rate_hz"])
        self.assertEqual(source["completed_jobs"]["rate_hz"], 60)

    def test_gpu_requires_clock_and_order(self):
        self.source("gpu", "input.csv")
        self.write("input.csv", GPU_HEADER)
        with self.assertRaisesRegex(ValueError, "gpu_clock_hz"):
            self.summarize()
        self.source("gpu", "input.csv", gpu_clock_hz=88000000)
        self.write("input.csv", GPU_HEADER +
                   "5,10,880000,0,10,0,100,200,200\n" +
                   "5,70,880000,0,10,0,160,230,230\n")
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            self.summarize()

    def test_empty_capture_does_not_claim_zero_cost(self):
        self.source("gpu", "input.csv", gpu_clock_hz=88000000)
        self.write("input.csv", GPU_HEADER)
        source = self.summarize()["samples"][0]["sources"][0]
        self.assertIsNone(source["sampled_underflow_flags"])
        self.assertEqual(source["gpu_job_time"]["status"], "no_observations")

    def test_gpu_uint32_overflow_cannot_wrap_into_plausible_cadence(self):
        self.source("gpu", "input.csv", gpu_clock_hz=88000000)
        self.write("input.csv", GPU_HEADER + "0,4294967296,880000,0,10,1,100,200,200\n")
        with self.assertRaisesRegex(ValueError, "uint32"):
            self.summarize()

    def test_identity_and_input_hash_validation(self):
        self.write("input.trace", "")
        self.manifest["identities"]["runner_sha256"] = "old runner"
        with self.assertRaisesRegex(ValueError, "runner_sha256"):
            self.summarize()
        self.manifest["identities"]["runner_sha256"] = None
        self.manifest["samples"][0]["sources"][0]["sha256"] = "b" * 64
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            self.summarize()

    def test_multiple_run_association_must_be_explicit(self):
        self.write("input.trace", "")
        other = copy.deepcopy(self.manifest["samples"][0])
        other.update(id="other", run_id="run-2")
        self.manifest["samples"].append(other)
        with self.assertRaisesRegex(ValueError, "independent-runs"):
            self.summarize()
        self.manifest["comparison"] = "independent-runs"
        self.assertEqual(len(self.summarize()["samples"]), 2)
        other["run_id"] = "run-1"
        with self.assertRaisesRegex(ValueError, "same-run sources"):
            self.summarize()

    def test_same_run_multiple_sources_must_be_explicit(self):
        self.write("input.trace", "")
        sample = self.manifest["samples"][0]
        sample["sources"].append({"kind": "events", "path": "input.trace"})
        with self.assertRaisesRegex(ValueError, "association"):
            self.summarize()
        sample["association"] = "same-run"
        self.assertEqual(len(self.summarize()["samples"][0]["sources"]), 2)

    def test_surface_optional_bytes_unknown_not_zero(self):
        self.source("surfaces", "input.csv")
        self.write("input.csv", "elapsed_ns,frame,surface_id,operation,calls,pixels,elapsed_work_ns,uploaded_bytes\n"
                   "0,1,6,user_axis,4,512,1000000,\n"
                   "16000000,2,6,user_axis,4,512,2000000,\n"
                   "16000000,2,6,upload,1,,100000,2048\n")
        groups = self.summarize()["samples"][0]["sources"][0]["groups"]
        axis = next(row for row in groups if row["operation"] == "user_axis")
        self.assertEqual(axis["elapsed_work_ns"]["median"], 1.5)
        self.assertEqual(axis["uploaded_bytes"]["status"], "no_observations")

    def test_cli_json_roundtrip_and_lost_trace_warning(self):
        self.write("input.trace", "# entries-in-buffer/entries-written: 2/8\n" +
                   event(1, "step") + event(1.010, "step_ret"))
        manifest = self.write("manifest.json", json.dumps(self.manifest))
        result = subprocess.run([sys.executable, str(SCRIPT), str(manifest), "--format", "json"],
                                text=True, capture_output=True, check=True)
        report = json.loads(result.stdout)
        self.assertTrue(any("retain" in warning for warning in report["samples"][0]["sources"][0]["warnings"]))

    def test_surface_drop_count_is_not_multiplied_by_operation_rows(self):
        self.source("surfaces", "input.csv")
        self.write("input.csv", "elapsed_ns,frame,surface_id,operation,calls,pixels,elapsed_work_ns,uploaded_bytes,room,input_mask,dropped_scopes\n"
                   "0,1,6,user_axis,4,512,1000000,,160,16,2\n"
                   "0,1,6,upload,1,,100000,2048,160,16,2\n"
                   "16000000,2,6,user_axis,4,512,2000000,,160,0,1\n")
        source = self.summarize()["samples"][0]["sources"][0]
        self.assertEqual(source["observed_frames"], 2)
        self.assertEqual(source["dropped_scopes"], 3)
        self.assertEqual(source["rooms"], ["160"])
        self.assertTrue(any("incomplete" in warning for warning in source["warnings"]))

    def test_malformed_nested_event_does_not_consume_outer_return(self):
        self.source("events")
        self.write("input.trace", event(1, "resolved_event", tail="object=727 event=3 subtype=0 code=2983 owner=727") +
                   event(1.001, "resolved_event", tail="object=0") +
                   event(1.003, "resolved_event_ret") + event(1.009, "resolved_event_ret"))
        rows = self.summarize()["samples"][0]["sources"][0]["paired_calls"]
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["inclusive"]["median"], 9)

    def test_multiple_game_pids_do_not_produce_one_frame_rate(self):
        self.write("input.trace", event(1, "step", pid=77) + event(1.001, "step", pid=88) +
                   event(1.016, "step", pid=77) + event(1.033, "step", pid=88))
        source = self.summarize()["samples"][0]["sources"][0]
        self.assertIsNone(source["game_tick_rate_hz"])
        self.assertEqual(source["step_pids"], ["77", "88"])


if __name__ == "__main__":
    unittest.main()
