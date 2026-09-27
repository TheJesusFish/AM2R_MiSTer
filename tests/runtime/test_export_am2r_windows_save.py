"""Synthetic-only coverage; no user saves or game assets are bundled."""

import base64
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import export_am2r_windows_save as export
from am2r_save_crypt import transform


def fixture():
    sections = []
    for count in export.COUNTS:
        fields = [struct.pack("<Id", 0, 12.5), struct.pack("<Ii", 7, -3),
                  struct.pack("<Id", 13, 1.0), struct.pack("<Iq", 10, 1234567)]
        fields += [struct.pack("<Ii", 7, 0)] * (count - len(fields))
        blob = struct.pack("<II", 303, count) + b"".join(fields)
        sections.append(base64.b64encode(blob.hex().upper().encode("ascii")))
    result = bytearray(b"\n".join([export.rc4(export.HEADER).encode("utf-8"), *sections]) + b"\n")
    transform(result)
    return bytes(result)


class WindowsSaveExportTest(unittest.TestCase):
    def test_export_preserves_all_values_and_uses_native_format(self):
        result = export.convert(fixture())
        sections = export.decode_save(result, export.WINDOWS_GAME_ID, native=True)
        for blob, count in zip(sections, export.COUNTS, strict=True):
            values = export.read_numeric_list(blob, 301, count)
            self.assertEqual(values[:4], [12.5, -3.0, 1.0, 1234567.0])
            self.assertEqual(values[4:], [0.0] * (count - 4))
        with self.assertRaises(ValueError):
            export.decode_save(result, export.GAME_ID)

    def test_native_key_zero_uses_first_character_not_empty(self):
        # Independent native 1.1 disassembly at 0x4c0090 clamps char_at(0)
        # to character 1. First UTF-8 header byte C3, game ID starts with '9',
        # key starts with 'X': C3 XOR 39 XOR 58 = A2, not old export's FA.
        converted = export.convert(fixture())
        self.assertEqual(converted[0], 0xA2)
        broken = bytearray(converted)
        broken[0] ^= 0x58
        with self.assertRaises(ValueError):
            export.decode_save(broken, export.WINDOWS_GAME_ID, native=True)

    def test_native_indexing_changes_only_first_byte(self):
        legacy = bytearray(b"x" * 20000)
        native = bytearray(legacy)
        transform(legacy, game_id=export.WINDOWS_GAME_ID)
        transform(native, game_id=export.WINDOWS_GAME_ID, native_char_at_zero=True)
        self.assertEqual(legacy[0] ^ native[0], 0x58)
        self.assertEqual(legacy[1:], native[1:])

    def test_refuses_corrupt_and_already_converted(self):
        damaged = bytearray(fixture())
        damaged[0] ^= 1
        with self.assertRaises(ValueError):
            export.convert(damaged)
        with self.assertRaises(ValueError):
            export.convert(export.convert(fixture()))

    def test_refuses_unsupported_lossy_and_trailing_list_data(self):
        for field in (struct.pack("<I", 5), struct.pack("<Iq", 10, 2**53 + 1),
                      struct.pack("<Id", 0, float("nan"))):
            with self.assertRaises(ValueError):
                export.read_numeric_list(struct.pack("<II", 303, 1) + field, 303, 1)
        with self.assertRaises(ValueError):
            export.read_numeric_list(struct.pack("<II", 303, 0) + b"extra", 303, 0)

    def test_no_overwrite_and_original_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, target = Path(temporary) / "sav1", Path(temporary) / "windows-sav1"
            original = fixture()
            source.write_bytes(original)
            with self.assertRaises(ValueError):
                export.export_file(source, source)
            result = export.export_file(source, target)
            self.assertTrue(result["source_unchanged"])
            converted = target.read_bytes()
            with self.assertRaises(FileExistsError):
                export.export_file(source, target)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(target.read_bytes(), converted)


if __name__ == "__main__":
    unittest.main()
