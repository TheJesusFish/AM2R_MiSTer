#!/usr/bin/env python3
"""Locate and characterize a known PCM fixture in a hardware capture."""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

import numpy as np


def read_wav(path: Path) -> tuple[int, np.ndarray]:
    data = path.read_bytes()
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError(f"{path}: not RIFF/WAVE")

    offset = 12
    fmt = None
    payload = None
    while offset + 8 <= len(data):
        name = data[offset : offset + 4]
        size = struct.unpack_from("<I", data, offset + 4)[0]
        chunk = data[offset + 8 : offset + 8 + size]
        if name == b"fmt ":
            tag, channels, rate, _, align, bits = struct.unpack_from("<HHIIHH", chunk)
            if tag == 0xFFFE and len(chunk) >= 40:
                tag = struct.unpack_from("<I", chunk, 24)[0]
            fmt = tag, channels, rate, align, bits
        elif name == b"data":
            payload = chunk
        offset += 8 + size + (size & 1)

    if fmt is None or payload is None:
        raise ValueError(f"{path}: missing fmt/data")
    tag, channels, rate, align, bits = fmt
    if tag == 3 and bits == 32:
        audio = np.frombuffer(payload, dtype="<f4")
    elif tag == 1 and bits == 16:
        audio = np.frombuffer(payload, dtype="<i2").astype(np.float64) / 32768.0
    else:
        raise ValueError(f"{path}: unsupported tag={tag} bits={bits}")
    if align != channels * (bits // 8):
        raise ValueError(f"{path}: invalid block alignment")
    return rate, audio.reshape(-1, channels).astype(np.float64)


def best_match(capture: np.ndarray, reference: np.ndarray, rate: int) -> dict:
    """Return normalized full-capture correlation using first differences."""
    step = 2
    capture = np.diff(capture, prepend=capture[0])[::step]
    reference = np.diff(reference, prepend=reference[0])[::step]
    reference -= np.mean(reference)
    count = len(reference)
    if len(capture) < count:
        raise ValueError("capture is shorter than reference")

    output_count = len(capture) + count - 1
    fft_count = 1 << (output_count - 1).bit_length()
    numerator = np.fft.irfft(
        np.fft.rfft(capture, fft_count) * np.fft.rfft(reference[::-1], fft_count),
        fft_count,
    )[:output_count]
    numerator = numerator[count - 1 : len(capture)]
    squares = np.concatenate(([0.0], np.cumsum(capture * capture)))
    sums = np.concatenate(([0.0], np.cumsum(capture)))
    energy = squares[count:] - squares[:-count]
    window_sum = sums[count:] - sums[:-count]
    energy -= window_sum * window_sum / count
    denominator = np.linalg.norm(reference) * np.sqrt(np.maximum(energy, 0.0))
    scores = np.divide(
        numerator, denominator, out=np.zeros_like(numerator), where=denominator > 0
    )
    index = int(np.argmax(scores))
    return {
        "correlation": float(scores[index]),
        "start_seconds": float(index * step / rate),
    }


def phase_correlations(
    capture: np.ndarray, reference: np.ndarray, rate: int, start_seconds: float
) -> dict:
    """Check whether only selected sample lanes survive the transport."""
    start = max(0, round(start_seconds * rate))
    margin = 64
    output = {}
    for modulus in (2, 4, 8):
        best = (-2.0, None)
        for capture_phase in range(modulus):
            for reference_phase in range(modulus):
                ref = reference[reference_phase::modulus]
                for lag in range(-margin, margin + 1):
                    candidate_start = start + lag + capture_phase
                    if candidate_start < 0:
                        continue
                    candidate = capture[
                        candidate_start : candidate_start + modulus * len(ref) : modulus
                    ]
                    count = min(len(candidate), len(ref))
                    left = candidate[:count] - np.mean(candidate[:count])
                    right = ref[:count] - np.mean(ref[:count])
                    denominator = np.linalg.norm(left) * np.linalg.norm(right)
                    score = float(np.dot(left, right) / denominator) if denominator else 0.0
                    if score > best[0]:
                        best = (
                            score,
                            {
                                "capture_phase": capture_phase,
                                "reference_phase": reference_phase,
                                "lag_samples": lag,
                            },
                        )
        output[str(modulus)] = {"correlation": best[0], **best[1]}
    return output


def reinterpret_s16(audio: np.ndarray, operation: str) -> np.ndarray:
    samples = np.clip(np.rint(audio * 32768.0), -32768, 32767).astype("<i2")
    if operation == "identity":
        output = samples
    elif operation == "byte_swap":
        output = samples.byteswap()
    elif operation == "adjacent_swap":
        output = samples.copy()
        usable = len(output) & ~1
        output[:usable:2], output[1:usable:2] = (
            samples[1:usable:2],
            samples[:usable:2],
        )
    elif operation == "byte_and_adjacent_swap":
        output = reinterpret_s16(audio, "adjacent_swap")
        return reinterpret_s16(output, "byte_swap")
    elif operation == "duplicate_even":
        output = np.repeat(samples[::2], 2)[: len(samples)]
    elif operation == "duplicate_odd":
        output = np.repeat(samples[1::2], 2)
        if len(output) < len(samples):
            output = np.pad(output, (0, len(samples) - len(output)))
    elif operation.startswith("frame4_"):
        order = tuple(int(value) for value in operation.removeprefix("frame4_").split("_"))
        framed = samples[: len(samples) // 4 * 4].reshape(-1, 4)
        output = framed[:, order].reshape(-1)
    else:
        raise ValueError(operation)
    return output.astype(np.float64) / 32768.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("reference", type=Path)
    parser.add_argument("--window-ms", type=float, default=100.0)
    args = parser.parse_args()

    capture_rate, capture = read_wav(args.capture)
    reference_rate, reference = read_wav(args.reference)
    if capture_rate != reference_rate:
        raise ValueError(f"rate mismatch: {capture_rate} != {reference_rate}")
    capture_mono = np.mean(capture, axis=1)
    reference_mono = np.mean(reference, axis=1)

    def basic_metrics(audio: np.ndarray) -> dict:
        spectrum = np.abs(np.fft.rfft(audio * np.hanning(len(audio)))) ** 2
        frequencies = np.fft.rfftfreq(len(audio), 1.0 / capture_rate)
        total = float(np.sum(spectrum)) or 1.0
        return {
            "peak": float(np.max(np.abs(audio))),
            "rms": float(np.sqrt(np.mean(audio * audio))),
            "spectral_centroid_hz": float(np.sum(frequencies * spectrum) / total),
            "energy_above_12khz_ratio": float(
                np.sum(spectrum[frequencies >= 12000]) / total
            ),
        }

    window = max(1, round(capture_rate * args.window_ms / 1000.0))
    usable = len(capture_mono) // window * window
    blocks = capture_mono[:usable].reshape(-1, window)
    rms = np.sqrt(np.mean(blocks * blocks, axis=1))
    peaks = np.max(np.abs(blocks), axis=1)
    spectra = np.abs(np.fft.rfft(blocks * np.hanning(window), axis=1))
    frequencies = np.fft.rfftfreq(window, 1.0 / capture_rate)
    dominant = frequencies[np.argmax(spectra, axis=1)]
    top = np.argsort(rms)[-20:][::-1]

    matches = {}
    for operation in (
        "identity",
        "byte_swap",
        "adjacent_swap",
        "byte_and_adjacent_swap",
        "duplicate_even",
        "duplicate_odd",
        "frame4_2_3_0_1",
        "frame4_3_2_1_0",
        "frame4_1_0_3_2",
    ):
        candidate = reinterpret_s16(reference_mono, operation)
        matches[operation] = best_match(capture_mono, candidate, capture_rate)

    rate_matches = []
    rate_search_start = max(
        0, round((matches["identity"]["start_seconds"] - 0.2) * capture_rate)
    )
    rate_capture = capture_mono[
        rate_search_start : rate_search_start + len(reference_mono) + capture_rate
    ]
    for ratio in np.linspace(0.990, 1.010, 81):
        positions = np.arange(0.0, len(reference_mono), ratio)
        candidate = np.interp(
            positions,
            np.arange(len(reference_mono), dtype=np.float64),
            reference_mono,
        )
        match = best_match(rate_capture, candidate, capture_rate)
        match["start_seconds"] += rate_search_start / capture_rate
        rate_matches.append({"source_step": float(ratio), **match})
    rate_matches.sort(key=lambda item: item["correlation"], reverse=True)
    best_rate_match = rate_matches[0]
    reference_capture_start = round(best_rate_match["start_seconds"] * capture_rate)
    reference_capture_count = round(
        len(reference_mono) / best_rate_match["source_step"]
    )
    captured_reference_window = capture_mono[
        reference_capture_start : reference_capture_start + reference_capture_count
    ]

    result = {
        "capture": str(args.capture),
        "reference": str(args.reference),
        "sample_rate": capture_rate,
        "capture_channels": capture.shape[1],
        "reference_channels": reference.shape[1],
        "capture_peak": float(np.max(np.abs(capture))),
        "reference_peak": float(np.max(np.abs(reference))),
        "reference_metrics": basic_metrics(reference_mono),
        "channel_correlation": (
            float(np.corrcoef(capture[:, 0], capture[:, 1])[0, 1])
            if capture.shape[1] >= 2
            else None
        ),
        "top_rms_windows": [
            {
                "start_seconds": float(index * window / capture_rate),
                "rms": float(rms[index]),
                "peak": float(peaks[index]),
                "dominant_hz": float(dominant[index]),
            }
            for index in top
        ],
        "reference_transform_matches": matches,
        "best_constant_rate_matches": rate_matches[:10],
        "captured_reference_window_metrics": basic_metrics(captured_reference_window),
        "phase_lane_matches": phase_correlations(
            capture_mono,
            reference_mono,
            capture_rate,
            matches["identity"]["start_seconds"],
        ),
    }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
