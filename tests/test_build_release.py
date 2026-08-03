import tempfile
import unittest
from pathlib import Path

from scripts.build_release import copy_entry, files_under


class ReleaseCopyTests(unittest.TestCase):
    def test_copy_entry_excludes_python_cache_files(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source"
            target = root / "target"
            cache = source / "module" / "__pycache__"
            cache.mkdir(parents=True)
            (source / "module" / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
            (cache / "feature.cpython-313.pyc").write_bytes(b"cache")
            (source / "module" / "legacy.pyo").write_bytes(b"optimized-cache")

            copy_entry(source, target)

            copied = [path.relative_to(target).as_posix() for path in files_under(target)]
            self.assertEqual(copied, ["module/feature.py"])


if __name__ == "__main__":
    unittest.main()
