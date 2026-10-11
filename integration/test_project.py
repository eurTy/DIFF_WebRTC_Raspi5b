import tempfile
import unittest
from pathlib import Path

from project import beneath, compare, digest, inventory


class IntegrationTests(unittest.TestCase):
    def test_manifest_resolves_all_modules(self):
        data = inventory()
        self.assertEqual({f["module"] for f in data["files"]}, {"capture", "rtc-gateway", "signaling", "observer"})
        self.assertGreater(len(data["files"]), 30)

    def test_path_boundary(self):
        with tempfile.TemporaryDirectory() as name:
            for unsafe in ("../secret", "/etc/passwd", "dir\\secret"):
                with self.assertRaises(ValueError):
                    beneath(Path(name), unsafe)

    def test_normalized_hash_and_mismatch(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            one, two = root / "one", root / "two"
            one.write_bytes(b"a\r\nb\r\n")
            two.write_bytes(b"a\nb\n")
            self.assertEqual(digest(one), digest(two))
            record = {"files": [{"runtime": "two", "sha256_lf": digest(one)}]}
            self.assertEqual(compare(root, record)["differences"], [])
            two.write_text("different")
            self.assertEqual(compare(root, record)["differences"][0]["state"], "DIFFERENT")
            two.unlink()
            self.assertEqual(compare(root, record)["differences"][0]["state"], "MISSING")


if __name__ == "__main__":
    unittest.main()
