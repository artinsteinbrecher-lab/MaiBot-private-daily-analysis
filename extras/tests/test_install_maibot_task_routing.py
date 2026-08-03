from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import os
import subprocess
import sys
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "install_maibot_task_routing.py"
TARGETS = (
    "src/config/model_configs.py",
    "src/config/default_model_config.py",
    "src/llm_models/utils_model.py",
    "src/config/config.py",
)

MODEL_CONFIGS = '''class ModelTaskConfig:
    expression_use: TaskConfig = Field(
        default_factory=TaskConfig,
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "message-circle-more",
            "advanced": True,
        },
    )
    """表达方式使用模型配置；留空时用 utils 模型"""

    emoji: TaskConfig = Field(default_factory=TaskConfig)
'''

DEFAULT_MODEL_CONFIG = '''DEFAULT_TASK_CONFIG_TEMPLATES = {
    "learner": {"model_list": [], "max_tokens": 4096, "hard_timeout": 120.0},
    "expression_use": {"model_list": [], "max_tokens": 1024, "temperature": 0.3, "hard_timeout": 120.0},
    "emoji": {"model_list": [], "max_tokens": 4096, "hard_timeout": 120.0},
}
'''

UTILS_MODEL = '''EMPTY_TASK_FALLBACKS = {
    "expression_use": "utils",
    "learner": "utils",
    "mid_memory": "planner",
}
'''

CONFIG = '''CONFIG_VERSION: str = "8.14.33"
MODEL_CONFIG_VERSION: str = "1.17.6"
'''


class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        files = {
            TARGETS[0]: MODEL_CONFIGS,
            TARGETS[1]: DEFAULT_MODEL_CONFIG,
            TARGETS[2]: UTILS_MODEL,
            TARGETS[3]: CONFIG,
        }
        for relative, text in files.items():
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="")
        self.before = {relative: (self.root / relative).read_bytes() for relative in TARGETS}

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_script(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(self.root), *args],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        )

    def snapshot(self) -> dict[str, bytes]:
        return {relative: (self.root / relative).read_bytes() for relative in TARGETS}

    def test_check_is_read_only(self) -> None:
        result = self.run_script("--check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("状态：pristine", result.stdout)
        self.assertEqual(self.snapshot(), self.before)
        self.assertFalse((self.root / ".khiqwq_daily_analysis_backups").exists())

    def test_apply_is_complete_and_idempotent(self) -> None:
        first = self.run_script("--apply")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("状态：应用成功", first.stdout)
        for task in (
            "plugin_daily_extract",
            "plugin_daily_compose",
            "plugin_daily_verify",
            "plugin_user_profile",
        ):
            self.assertIn(task, (self.root / TARGETS[0]).read_text(encoding="utf-8"))
            self.assertIn(task, (self.root / TARGETS[1]).read_text(encoding="utf-8"))
            self.assertIn(task, (self.root / TARGETS[2]).read_text(encoding="utf-8"))
        self.assertIn(
            'MODEL_CONFIG_VERSION: str = "1.17.7"',
            (self.root / TARGETS[3]).read_text(encoding="utf-8"),
        )
        backups = list((self.root / ".khiqwq_daily_analysis_backups").iterdir())
        self.assertEqual(len(backups), 1)

        second = self.run_script("--apply")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("已安装，无需重复修改", second.stdout)
        self.assertEqual(len(list((self.root / ".khiqwq_daily_analysis_backups").iterdir())), 1)

    def test_unknown_version_and_partial_state_are_rejected(self) -> None:
        config_path = self.root / TARGETS[3]
        config_path.write_text(CONFIG.replace("1.17.6", "9.9.9"), encoding="utf-8", newline="")
        unknown = self.run_script("--apply")
        self.assertEqual(unknown.returncode, 2)
        self.assertIn("仅支持模型配置版本", unknown.stderr)
        self.assertFalse((self.root / ".khiqwq_daily_analysis_backups").exists())

        config_path.write_text(CONFIG, encoding="utf-8", newline="")
        default_path = self.root / TARGETS[1]
        default_path.write_text(DEFAULT_MODEL_CONFIG + '\n"plugin_daily_extract": {}\n', encoding="utf-8", newline="")
        partial = self.run_script("--apply")
        self.assertEqual(partial.returncode, 2)
        self.assertIn("部分安装", partial.stderr)
        self.assertFalse((self.root / ".khiqwq_daily_analysis_backups").exists())

    def test_rollback_restores_exact_original_bytes(self) -> None:
        applied = self.run_script("--apply")
        self.assertEqual(applied.returncode, 0, applied.stderr)
        backup_line = next(line for line in applied.stdout.splitlines() if line.startswith("BACKUP_DIR="))
        backup = backup_line.split("=", 1)[1]
        rolled_back = self.run_script("--rollback", backup)
        self.assertEqual(rolled_back.returncode, 0, rolled_back.stderr)
        self.assertIn("状态：回滚成功", rolled_back.stdout)
        self.assertEqual(self.snapshot(), self.before)


if __name__ == "__main__":
    unittest.main()
