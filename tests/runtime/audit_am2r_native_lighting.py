#!/usr/bin/env python3
"""Lock the hardware-validated AM2R lighting optimization boundary."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "third_party" / "Butterscotch" / "src" / "runner.c"


def require(source: str, text: str, description: str) -> None:
    if text not in source:
        raise SystemExit(f"AM2R lighting audit failed: {description}")


def main() -> int:
    source = RUNNER.read_text(encoding="utf-8")

    # Normal Step only updates fade state and redundantly rebuilds the mask.
    # End Step performs the authored rebuild consumed by Draw, so it is safe to
    # skip that first rebuild after validating the exact AM2R 1.1 handlers.
    require(source, '"gml_Object_oLightEngine_Step_0"',
            "Normal Step identity guard is missing")
    require(source, '"gml_Object_oLightEngine_Step_2"',
            "End Step companion guard is missing")
    require(source,
            "findEventCodeIdAndOwner(\n        runner, instance->objectIndex, EVENT_STEP, STEP_END",
            "Normal Step no longer validates the authored End Step rebuild")
    require(source, "instance->imageAlpha - (GMLReal)0.01",
            "Normal Step fade-out semantics are missing")
    require(source,
            "Runner_tryExecuteAm2rLightStep(\n            runner, instance, eventType, eventSubtype, codeId,",
            "validated native Normal Step is not dispatched")

    # USB-1 A/B testing showed that replacing Other 11's interpreted producer
    # sequence with a native C implementation added hitches. Keep the authored
    # bytecode path until a replacement beats it on real hardware.
    forbidden = (
        "Runner_tryExecuteAm2rLightOther",
        "Am2rLightProducer",
        '"gml_Object_oLightEngine_Other_11"',
    )
    present = [token for token in forbidden if token in source]
    if present:
        raise SystemExit(
            "AM2R lighting audit failed: rejected native Other-11 path returned: "
            + ", ".join(present))

    require(source,
            "if (!handledNatively)\n#endif\n        executeCode(runner, instance, codeId);",
            "bytecode fallback is no longer retained")

    print("AM2R lighting audit passed: redundant Normal Step is removed, "
          "fade semantics are retained, and Other 11 stays on bytecode")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
