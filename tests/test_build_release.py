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
        downloads = (root / "docs" / "DOWNLOADS.md").read_text(encoding="utf-8")

        self.assertIn(f"/releases/tag/v{version}", downloads)
        self.assertIn(f"khiqwq_daily_analysis-v{version}-", downloads)

        packaging = root / "packaging"
        if packaging.exists():
            for variant in ("standard", "multimodel"):
                filename = f"khiqwq_daily_analysis-v{version}-{variant}.zip"
                self.assertIn(f"/releases/download/v{version}/{filename}", downloads)
                variant_readme = (packaging / variant / "README.md").read_text(
                    encoding="utf-8"
                )
                self.assertIn(filename, variant_readme)
                self.assertIn(f"/releases/tag/v{version}", variant_readme)

    def test_primary_document_navigation_links_exist(self):
        root = Path(__file__).resolve().parents[1]
        for markdown in (
            root / "README.md",
            root / "docs" / "README.md",
            root / "docs" / "DOWNLOADS.md",
            root / "docs" / "LEGACY_SILENCE.md",
            root / "packaging" / "standard" / "README.md",
            root / "packaging" / "multimodel" / "README.md",
            root / "extras" / "README.md",
        ):
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
        self.assertIn("麦麦安静写书", readme)
        self.assertIn("麦麦安静写书", guide)
        self.assertIn("不能同时", readme)
        self.assertIn("其他插件发往该群的消息", guide)
        self.assertLess(
            readme.index("麦麦安静写书"),
            readme.index("如果你想换一种用法"),
        )
        self.assertIn("麦麦群安静插件", readme)
        self.assertIn("麦麦认真写书", readme)
        self.assertIn("麦麦一起写书", readme)
        self.assertIn("/mysummary", readme)

    def test_download_page_and_three_version_guides_are_complete(self):
        root = Path(__file__).resolve().parents[1]
        downloads = (root / "docs" / "DOWNLOADS.md").read_text(encoding="utf-8")
        quiet = (root / "docs" / "LEGACY_SILENCE.md").read_text(encoding="utf-8")
        standard = (root / "packaging" / "standard" / "README.md").read_text(
            encoding="utf-8"
        )
        multimodel = (root / "packaging" / "multimodel" / "README.md").read_text(
            encoding="utf-8"
        )
        extras = (root / "extras" / "README.md").read_text(encoding="utf-8")

        for edition_name in ("麦麦安静写书", "麦麦认真写书", "麦麦一起写书"):
            self.assertIn(edition_name, downloads)
        self.assertIn("安装只要三步", quiet)
        self.assertIn("主要事件", standard)
        self.assertIn("普通话题", standard)
        self.assertIn("五分钟安装", standard)
        self.assertIn("第一阶段：先按普通插件验证", multimodel)
        self.assertIn("第二阶段：可选启用四任务路由", multimodel)
        self.assertIn("install_maibot_task_routing.py /path/to/MaiBot --check", extras)
        self.assertIn("install_maibot_task_routing.py /path/to/MaiBot --apply", extras)
        for task_name in (
            "plugin_daily_extract",
            "plugin_daily_compose",
            "plugin_daily_verify",
            "plugin_user_profile",
        ):
            self.assertIn(task_name, multimodel)

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
