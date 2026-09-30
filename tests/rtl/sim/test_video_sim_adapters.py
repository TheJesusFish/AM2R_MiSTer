#!/usr/bin/env python3
"""Check adapter provenance guards and preservation of the exercised logic."""
import unittest

from make_sim_video_mixer import PINNED, ROOT, adapt_sources


class VideoAdapterTests(unittest.TestCase):
    def setUp(self):
        self.sources = {name: (ROOT / "sys" / name).read_text(encoding="utf-8") for name in PINNED}

    def test_known_sources_and_unchanged_fast_gamma(self):
        result = adapt_sources(self.sources)
        self.assertEqual(result["gamma_corr_sim.sv"].split("module gamma_fast", 1)[1],
                         self.sources["gamma_corr.sv"].split("module gamma_fast", 1)[1])
        self.assertIn('if (scandoubler === 1\'b1)', result["mixer"])
        self.assertIn('if (HALF_DEPTH != 0)', result["mixer"])
        for name, source in self.sources.items():
            self.assertEqual((ROOT / "sys" / name).read_text(encoding="utf-8"), source)

    def test_rejects_each_changed_upstream_source(self):
        for name in PINNED:
            with self.subTest(name=name):
                changed = dict(self.sources)
                changed[name] += "\n// unknown upstream revision\n"
                with self.assertRaisesRegex(ValueError, "unsupported sys/"):
                    adapt_sources(changed)


if __name__ == "__main__":
    unittest.main()
