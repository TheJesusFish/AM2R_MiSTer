#!/usr/bin/env python3
"""Summarize calls and inclusive durations from capture_one_uprobe.sh."""

from __future__ import annotations

import argparse
import re
import statistics
from pathlib import Path


EVENT = re.compile(r"\s(?P<time>\d+\.\d+):\s+am2r_target(?P<ret>_ret)?:")


def percentile(values: list[float], fraction: float) -> float:
	ordered = sorted(values)
	return ordered[min(len(ordered) - 1, round(fraction * (len(ordered) - 1)))]


def main() -> int:
	parser = argparse.ArgumentParser()
	parser.add_argument("trace", type=Path)
	args = parser.parse_args()
	stack: list[float] = []
	entries: list[float] = []
	durations: list[float] = []
	for line in args.trace.read_text(errors="replace").splitlines():
		match = EVENT.search(line)
		if not match:
			continue
		timestamp = float(match.group("time")) * 1000.0
		if match.group("ret"):
			if stack:
				durations.append(timestamp - stack.pop())
		else:
			entries.append(timestamp)
			stack.append(timestamp)
	print(f"calls={len(entries)} returns={len(durations)}")
	if entries:
		span = max(entries[-1] - entries[0], .001)
		print(f"call_rate={len(entries) * 1000.0 / span:.3f}/s")
	if durations:
		print(
			f"duration_ms min={min(durations):.4f} "
			f"median={statistics.median(durations):.4f} "
			f"p95={percentile(durations, .95):.4f} "
			f"p99={percentile(durations, .99):.4f} "
			f"max={max(durations):.4f} total={sum(durations):.3f}"
		)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
