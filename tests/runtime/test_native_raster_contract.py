"""Lock the physical raster clock/width contract, beyond pixel-count tests."""

from fractions import Fraction
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]


def raster_periods(pll: str, video: str) -> dict[str, Fraction]:
    frequency = re.search(r'\.output_clock_frequency0\("([0-9.]+) MHz"\)', pll)
    if frequency is None:
        raise ValueError("Missing video PLL frequency")
    constants = dict(re.findall(r"localparam integer (\w+) = (\d+);", video))
    pixel_hz = Fraction(frequency[1]) * 1_000_000 / int(constants["CE_DIV"])
    pixel_us = 1_000_000 / pixel_hz
    return {
        "active_us": int(constants["H_ACTIVE"]) * pixel_us,
        "front_us": int(constants["H_FP"]) * pixel_us,
        "sync_us": int(constants["H_SYNC"]) * pixel_us,
        "ntsc_line_us": int(constants["H_TOTAL_60"]) * pixel_us,
        "pal_line_us": int(constants["H_TOTAL_50"]) * pixel_us,
        "ntsc_frame_us": int(constants["H_TOTAL_60"]) * int(constants["V_TOTAL_60"]) * pixel_us,
        "pal_frame_us": int(constants["H_TOTAL_50"]) * int(constants["V_TOTAL_50"]) * pixel_us,
    }


EXPECTED = {
    "active_us": Fraction("51.2"),
    "front_us": Fraction("2.56"),
    "sync_us": Fraction("4.8"),
    "ntsc_line_us": Fraction("63.68"),
    "pal_line_us": Fraction("64"),
    "ntsc_frame_us": Fraction("16684.16"),
    "pal_frame_us": Fraction("19968"),
}


class NativeRasterContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pll = (ROOT / "rtl/pll_video_0002.v").read_text()
        cls.video = (ROOT / "rtl/am2r_native_video.sv").read_text()

    def test_physical_clock_and_raster_match_restored_baseline(self):
        self.assertEqual(raster_periods(self.pll, self.video), EXPECTED)

    def test_pal_heartbeat_remains_derived_from_ntsc_raster(self):
        self.assertRegex(self.video, r"PACE_PERIOD_50\s*=\s*CE_DIV\s*\*\s*H_TOTAL_60\s*\*\s*V_TOTAL_60\s*;")

    def test_clock_only_narrowing_is_detected(self):
        narrowed = self.pll.replace('"25.000000 MHz"', '"26.822916 MHz"')
        self.assertNotEqual(narrowed, self.pll)
        self.assertNotEqual(raster_periods(narrowed, self.video), EXPECTED)

    def test_old_narrow_raster_totals_are_detected(self):
        narrowed = self.video.replace("H_TOTAL_60 = 398", "H_TOTAL_60 = 427")
        narrowed = narrowed.replace("H_TOTAL_50 = 400", "H_TOTAL_50 = 429")
        self.assertNotEqual(narrowed, self.video)
        self.assertNotEqual(raster_periods(self.pll, narrowed), EXPECTED)


if __name__ == "__main__":
    unittest.main()
