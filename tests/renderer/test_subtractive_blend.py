#!/usr/bin/env python3
"""Bit-exact checks for the MiSTer subtractive-lighting NEON arithmetic."""

from __future__ import annotations

import random


def fast_divide_by_255(value: int) -> int:
    return (value + 1 + (value >> 8)) >> 8


def scalar_channel(source: int, tint: int, source_alpha: int, tint_alpha: int) -> int:
    alpha = source_alpha * tint_alpha // 255
    tinted = source * tint // 255
    return tinted * alpha // 255


def vector_channel(source: int, tint: int, source_alpha: int, tint_alpha: int) -> int:
    alpha = fast_divide_by_255(source_alpha * tint_alpha)
    tinted = fast_divide_by_255(source * tint)
    return fast_divide_by_255(tinted * alpha)


def main() -> None:
    for value in range(255 * 255 + 1):
        assert fast_divide_by_255(value) == value // 255

    generator = random.Random(0xA2_4D_52)
    for _ in range(250_000):
        source = generator.randrange(256)
        tint = generator.randrange(256)
        source_alpha = generator.randrange(256)
        tint_alpha = generator.randrange(256)
        assert vector_channel(source, tint, source_alpha, tint_alpha) == scalar_channel(
            source, tint, source_alpha, tint_alpha
        )

    print("AM2R subtractive blend arithmetic passed: exhaustive /255 and 250000 modulated channels")


if __name__ == "__main__":
    main()
