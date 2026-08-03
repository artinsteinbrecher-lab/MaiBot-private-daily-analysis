import json
import re
import tempfile
import unittest
from pathlib import Path

from scripts.build_release import copy_entry, files_under, validate_version


class ReleaseCopyTests(unittest.TestCase):
    def test_release_documentation_matches_manifest_version(self):
        root = Path(__file__).resolve().parents[1]
        manifest = json.loads((root / "_manifest.json").read_text(encoding="utf-8"))
        version = validate_version(manifest["version"])
        readme = (root / "README.md").read_text(encoding="utf-8")

        self.assertIn(f"/releases/tag/v{version}", readme)
        self.assertIn(f"khiqwq_daily_analysis-v{version}-", readme)

        packaging = root / "packaging"
        if packaging.exists():
            for variant in ("standard", "multimodel"):
                filename = f"khiqwq_daily_analysis-v{version}-{variant}.zip"
                self.assertIn(f"/releases/download/v{version}/{filename}", readme)
                variant_readme = (packaging / variant / "README.md").read_text(
                    encoding="utf-8"
                )
                self.assertIn(filename, variant_readme)
                self.assertIn(f"/releases/tag/v{version}", variant_readme)

    def test_primary_document_navigation_links_exist(self):
        root = Path(__file__).resolve().parents[1]
        for markdown in (root / "README.md", root / "docs" / "README.md"):
            if not markdown.exists():
                continue
            text = markdown.read_text(encoding="utf-8")
            for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
                if target.startswith(("http://", "https://", "#")):
                    continue
                path_text = target.split("#", 1)[0]
                if not path_text:
                    continue
                resolved = (markdown.parent / path_text).resolve()
                self.assertTrue(resolved.exists(), f"broken link in {markdown}: {target}")

    def test_legacy_silence_download_is_clearly_documented(self):
        root = Path(__file__).resolve().parents[1]
        legacy_doc = root / "docs" / "LEGACY_SILENCE.md"
        if not legacy_doc.exists():
            return

        filename = "khiqwq_daily_analysis-v3.1.0-legacy-silence.zip"
        download = f"/releases/download/v3.1.0/{filename}"
        readme = (root / "README.md").read_text(encoding="utf-8")
        guide = legacy_doc.read_text(encoding="utf-8")
        self.assertIn(download, readme)
        self.assertIn(download, guide)
        self.assertIn("不能同时", readme)
        self.assertIn("其他插件发往该群的消息", guide)

    def test_manifest_version_must_be_semver(self):
        self.assertEqual(validate_version("3.6.0"), "3.6.0")
        self.assertEqual(validate_version("3.7.0-rc.1"), "3.7.0-rc.1")
        with self.assertRaises(RuntimeError):
            validate_version("release-3.6")

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
