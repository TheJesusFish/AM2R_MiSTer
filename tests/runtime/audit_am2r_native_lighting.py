#!/usr/bin/env python3
"""Preserve AM2R's authored moving-light mask producer and repair events."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "third_party" / "Butterscotch" / "src" / "runner.c"


def require(source: str, text: str, description: str) -> None:
    if text not in source:
        raise SystemExit(f"AM2R lighting audit failed: {description}")


def main() -> int:
    source = RUNNER.read_text(encoding="utf-8")

    # Exact AM2R 1.1 Step0 calls user1 unconditionally; Step2 calls user0+user1
    # ONLY if !surface_exists(surf), branching to EOF otherwise. Skipping Step0
    # leaves an existing surface stale/white. Preserve Step0, Step2 and Other11
    # as authored, including fade-out and recovery after a lost surface.
    forbidden = (
        "Runner_tryExecuteAm2rLightStep",
        "Runner_tryExecuteAm2rLightOther",
        "Am2rLightProducer",
        '"gml_Object_oLightEngine_Step_0"',
        '"gml_Object_oLightEngine_Step_2"',
        '"gml_Object_oLightEngine_Other_11"',
    )
    present = [token for token in forbidden if token in source]
    if present:
        raise SystemExit(
            "AM2R lighting audit failed: authored lighting handler intercepted: "
            + ", ".join(present))

    require(source,
            "if (!handledNatively)\n#endif\n        executeCode(runner, instance, codeId);",
            "bytecode fallback is no longer retained")

    require(source, "Runner_tryExecuteAm2rWaterDraw", "unrelated water fast path removed")
    require(source, "Runner_tryExecuteAm2rSandDraw", "unrelated sand fast path removed")
    print("AM2R lighting audit passed: authored Normal Step, End Step repair, "
          "fade behavior and Other 11 remain on bytecode; water/sand fast paths retained")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
