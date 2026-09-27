"""Regression tests for build-set attestation and device hash checking."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "artifact_provenance.py"


def invoke(*args: object, stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *(str(arg) for arg in args)],
        input=stdin, text=True, capture_output=True, check=False,
    )


class ArtifactProvenanceTest(unittest.TestCase):
    def test_seal_requires_fresh_output_and_detects_later_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact, token, sidecar = (root / name for name in ("AM2R.rbf", "token.json", "AM2R.rbf.provenance.json"))
            artifact.write_bytes(b"old")
            self.assertEqual(invoke("begin", "fpga", artifact, token).returncode, 0)
            self.assertNotEqual(invoke("seal", token, artifact, sidecar).returncode, 0)
            artifact.write_bytes(b"new FPGA output")
            self.assertEqual(invoke("seal", token, artifact, sidecar).returncode, 0)
            self.assertEqual(invoke("verify", sidecar, artifact, "--check-source").returncode, 0)
            self.assertEqual(invoke("seal", token, artifact, sidecar).returncode, 0)
            artifact.write_bytes(b"altered")
            self.assertNotEqual(invoke("verify", sidecar, artifact).returncode, 0)

    def test_device_check_rejects_mixed_build(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = {name: root / name for name in ("fpga", "frontend", "runner")}
            for name, path in artifacts.items():
                path.write_bytes(name.encode())
            manifest = root / "observed.json"
            result = invoke("observe", manifest, "--fpga", artifacts["fpga"],
                            "--frontend", artifacts["frontend"], "--runner", artifacts["runner"],
                            "--rbf-install-path", "/media/fat/_Dev/AM2R.rbf")
            self.assertEqual(result.returncode, 0, result.stderr)
            record = json.loads(manifest.read_text())
            self.assertFalse(record["attested"])
            checksums = "".join(
                f"{hashlib.sha256(name.encode()).hexdigest()}  {item['install_path']}\n"
                for name, item in record["components"].items()
            )
            self.assertEqual(invoke("verify-device", manifest, stdin=checksums).returncode, 0)
            bad = checksums.replace(hashlib.sha256(b"runner").hexdigest(), "0" * 64)
            self.assertNotEqual(invoke("verify-device", manifest, stdin=bad).returncode, 0)


if __name__ == "__main__":
    unittest.main()
