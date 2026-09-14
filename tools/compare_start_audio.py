#!/usr/bin/env python3
"""Compare title-screen Start transients in two 32-bit-float WAV captures."""

import argparse
import json
import struct
from pathlib import Path

import numpy as np


def read_float_wav(path: Path):
    data = path.read_bytes()
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError(f"{path}: not a RIFF/WAVE file")
    offset = 12
    fmt = None
    samples = None
    while offset + 8 <= len(data):
        name = data[offset : offset + 4]
        size = struct.unpack_from("<I", data, offset + 4)[0]
        payload = data[offset + 8 : offset + 8 + size]
        if name == b"fmt ":
            tag, channels, rate, _, align, bits = struct.unpack_from("<HHIIHH", payload)
            if tag == 0xFFFE and len(payload) >= 40:
                # First DWORD of the extensible sub-format GUID is the legacy
                # WAVE format tag (3 is IEEE float).
                tag = struct.unpack_from("<I", payload, 24)[0]
            fmt = (tag, channels, rate, align, bits)
        elif name == b"data":
            samples = payload
        offset += 8 + size + (size & 1)
    if fmt is None or samples is None:
        raise ValueError(f"{path}: missing fmt or data chunk")
    tag, channels, rate, align, bits = fmt
    if tag != 3 or bits != 32 or align != channels * 4:
        raise ValueError(f"{path}: expected float32 PCM, got tag={tag} bits={bits}")
    audio = np.frombuffer(samples, dtype="<f4").reshape(-1, channels).astype(np.float64)
    return rate, audio


def transient_time(mono, rate, lo, hi):
    # A first difference suppresses the continuous title music and emphasizes
    # the broadband menu-accept transient. Rank 20 ms windows by high-passed
    # energy relative to their 200 ms neighborhood.
    differentiated = np.diff(mono, prepend=mono[0])
    window = max(1, rate // 50)
    power = differentiated * differentiated
    kernel = np.ones(window, dtype=np.float64) / window
    energy = np.convolve(power, kernel, mode="same")
    start = max(0, int(lo * rate))
    end = min(len(mono), int(hi * rate))
    index = start + int(np.argmax(energy[start:end]))
    return index / rate, float(energy[index])


def segment(mono, rate, center, before=0.15, after=0.85):
    start = max(0, int((center - before) * rate))
    end = min(len(mono), int((center + after) * rate))
    return mono[start:end]


def metrics(values, rate):
    peak = float(np.max(np.abs(values)))
    rms = float(np.sqrt(np.mean(values * values)))
    spectrum = np.abs(np.fft.rfft(values * np.hanning(len(values)))) ** 2
    frequencies = np.fft.rfftfreq(len(values), 1.0 / rate)
    total = float(np.sum(spectrum)) or 1.0
    return {
        "peak": peak,
        "rms": rms,
        "crest_factor": peak / rms if rms else 0.0,
        "clipped_samples": int(np.count_nonzero(np.abs(values) >= 0.999)),
        "spectral_centroid_hz": float(np.sum(frequencies * spectrum) / total),
        "energy_above_12khz_ratio": float(np.sum(spectrum[frequencies >= 12000]) / total),
    }


def best_correlation(a, b, rate):
    # Downsample to make the bounded lag search inexpensive; both sources have
    # already been converted to 48 kHz before this script is run.
    step = 4
    a = a[::step]
    b = b[::step]
    search_rate = rate // step
    length = min(len(a), len(b)) - int(0.12 * search_rate)
    a = a[:length]
    b = b[:length]
    best = (-2.0, 0)
    max_lag = int(0.12 * search_rate)
    for lag in range(-max_lag, max_lag + 1):
        if lag < 0:
            left, right = a[-lag:], b[: len(a) + lag]
        elif lag > 0:
            left, right = a[: len(a) - lag], b[lag:]
        else:
            left, right = a, b
        left = left - np.mean(left)
        right = right - np.mean(right)
        denominator = np.linalg.norm(left) * np.linalg.norm(right)
        correlation = float(np.dot(left, right) / denominator) if denominator else 0.0
        if correlation > best[0]:
            best = (correlation, lag)
    return {"normalized": best[0], "lag_ms": 1000.0 * best[1] / search_rate}


def fft_convolve(left, right):
    output_count = len(left) + len(right) - 1
    fft_count = 1 << (output_count - 1).bit_length()
    return np.fft.irfft(
        np.fft.rfft(left, fft_count) * np.fft.rfft(right, fft_count), fft_count
    )[:output_count]


def match_reference(capture, reference, rate, event_time=None):
    # Match first differences so the continuously playing title music has less
    # influence. Evaluate normalized correlation over a bounded event window.
    step = 2
    reference = np.diff(reference, prepend=reference[0])[::step]
    if event_time is None:
        search_start = 0
        search_end = len(capture)
    else:
        search_start = max(0, int((event_time - 0.5) * rate))
        search_end = min(len(capture), int((event_time + 1.7) * rate))
    search = np.diff(capture[search_start:search_end], prepend=capture[search_start])[::step]
    reference = reference - np.mean(reference)
    if len(search) < len(reference):
        return None
    numerator = fft_convolve(search, reference[::-1])
    numerator = numerator[len(reference) - 1 : len(search)]
    squares = np.concatenate(([0.0], np.cumsum(search * search)))
    sums = np.concatenate(([0.0], np.cumsum(search)))
    count = len(reference)
    energy = squares[count:] - squares[:-count]
    window_sum = sums[count:] - sums[:-count]
    energy -= window_sum * window_sum / count
    denominator = np.linalg.norm(reference) * np.sqrt(np.maximum(energy, 0.0))
    scores = np.divide(
        numerator,
        denominator,
        out=np.zeros_like(numerator),
        where=denominator > 0,
    )
    index = int(np.argmax(scores))
    absolute_time = search_start / rate + index * step / rate
    result = {
        "normalized": float(scores[index]),
        "start_seconds": absolute_time,
        "reference_duration_seconds": len(reference) * step / rate,
    }
    if event_time is not None:
        result["offset_from_detected_event_ms"] = 1000.0 * (absolute_time - event_time)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mister", type=Path)
    parser.add_argument("native", type=Path)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--mister-search", nargs=2, type=float, default=(35.0, 45.0))
    parser.add_argument("--native-search", nargs=2, type=float, default=(42.4, 44.4))
    args = parser.parse_args()

    mister_rate, mister_audio = read_float_wav(args.mister)
    native_rate, native_audio = read_float_wav(args.native)
    if mister_rate != native_rate:
        raise ValueError(f"sample-rate mismatch: {mister_rate} vs {native_rate}")
    mister_mono = np.mean(mister_audio, axis=1)
    native_mono = np.mean(native_audio, axis=1)
    mister_time, mister_energy = transient_time(mister_mono, mister_rate, *args.mister_search)
    native_time, native_energy = transient_time(native_mono, native_rate, *args.native_search)
    mister_event = segment(mister_mono, mister_rate, mister_time)
    native_event = segment(native_mono, native_rate, native_time)
    count = min(len(mister_event), len(native_event))
    result = {
        "sample_rate": mister_rate,
        "mister_event_seconds": mister_time,
        "native_event_seconds": native_time,
        "mister_transient_energy": mister_energy,
        "native_transient_energy": native_energy,
        "mister": metrics(mister_event[:count], mister_rate),
        "native": metrics(native_event[:count], native_rate),
        "waveform_correlation": best_correlation(
            np.diff(mister_event[:count]), np.diff(native_event[:count]), mister_rate
        ),
    }
    if args.reference is not None:
        reference_rate, reference_audio = read_float_wav(args.reference)
        if reference_rate != mister_rate:
            raise ValueError(f"reference sample-rate mismatch: {reference_rate} vs {mister_rate}")
        reference_mono = np.mean(reference_audio, axis=1)
        result["embedded_reference"] = {
            "mister": match_reference(mister_mono, reference_mono, mister_rate, mister_time),
            "native": match_reference(native_mono, reference_mono, native_rate, native_time),
            "mister_full_capture": match_reference(mister_mono, reference_mono, mister_rate),
            "native_full_capture": match_reference(native_mono, reference_mono, native_rate),
        }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
