#!/usr/bin/env python3
"""为受支持的 MaiBot 版本安装日报插件四任务模型路由。"""

from __future__ import annotations

from argparse import ArgumentParser, Namespace
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Dict, Tuple
import json
import os
import re
import sys

BASE_MODEL_CONFIG_VERSION = "1.17.6"
PATCHED_MODEL_CONFIG_VERSION = "1.17.7"
TARGET_FILES = (
    "src/config/model_configs.py",
    "src/config/default_model_config.py",
    "src/llm_models/utils_model.py",
    "src/config/config.py",
)
TASKS = (
    "plugin_daily_extract",
    "plugin_daily_compose",
    "plugin_daily_verify",
    "plugin_user_profile",
)

MODEL_CONFIG_FIELD_BLOCK = '''    plugin_daily_extract: TaskConfig = Field(
        default_factory=TaskConfig,
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "search",
            "advanced": True,
        },
    )
    """插件专用：群聊事实、事件与证据提取；留空时自动继用 replyer 模型"""

    plugin_daily_compose: TaskConfig = Field(
        default_factory=TaskConfig,
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "layers",
            "advanced": True,
        },
    )
    """插件专用：主要事件编排、压缩与去重；留空时自动继用 replyer 模型"""

    plugin_daily_verify: TaskConfig = Field(
        default_factory=TaskConfig,
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "shield-check",
            "advanced": True,
        },
    )
    """插件专用：独立事实复核；留空时自动继用 replyer 模型"""

    plugin_user_profile: TaskConfig = Field(
        default_factory=TaskConfig,
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "user-search",
            "advanced": True,
        },
    )
    """插件专用：事实型个人画像；留空时自动继用 replyer 模型"""
'''

DEFAULT_TASK_BLOCK = '''    "plugin_daily_extract": {"model_list": [], "max_tokens": 8192, "temperature": 0.1, "hard_timeout": 360.0},
    "plugin_daily_compose": {"model_list": [], "max_tokens": 3200, "temperature": 0.1, "hard_timeout": 240.0},
    "plugin_daily_verify": {"model_list": [], "max_tokens": 2500, "temperature": 0.0, "hard_timeout": 240.0},
    "plugin_user_profile": {"model_list": [], "max_tokens": 6000, "temperature": 0.2, "hard_timeout": 300.0},
'''

FALLBACK_TASK_BLOCK = '''    "plugin_daily_extract": "replyer",
    "plugin_daily_compose": "replyer",
    "plugin_daily_verify": "replyer",
    "plugin_user_profile": "replyer",
'''


class InstallError(RuntimeError):
    """兼容性、状态或安全校验失败。"""


@dataclass(frozen=True)
class Inspection:
    state: str
    version: str
    details: Tuple[str, ...]


def _sha256(data: bytes) -> str:
    return sha256(data).hexdigest()


def _target_paths(root: Path) -> Dict[str, Path]:
    root = root.resolve()
    paths: Dict[str, Path] = {}
    for relative in TARGET_FILES:
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise InstallError(f"目标路径越界：{target}") from exc
        if not target.is_file():
            raise InstallError(f"缺少 MaiBot 文件：{relative}")
        paths[relative] = target
    return paths


def _read_texts(root: Path) -> Tuple[Dict[str, Path], Dict[str, str], Dict[str, bytes]]:
    paths = _target_paths(root)
    texts: Dict[str, str] = {}
    raw: Dict[str, bytes] = {}
    for relative, target in paths.items():
        data = target.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InstallError(f"文件不是 UTF-8，拒绝修改：{relative}") from exc
        raw[relative] = data
        texts[relative] = text
    return paths, texts, raw


def _model_config_version(config_text: str) -> str:
    matches = re.findall(r'^MODEL_CONFIG_VERSION:\s*str\s*=\s*"([^"]+)"\s*$', config_text, re.MULTILINE)
    if len(matches) != 1:
        raise InstallError("无法唯一识别 MODEL_CONFIG_VERSION，拒绝修改")
    return matches[0]


def inspect(root: Path) -> Inspection:
    _, texts, _ = _read_texts(root)
    version = _model_config_version(texts["src/config/config.py"])
    checks = []
    for task in TASKS:
        checks.extend(
            (
                f"{task}: TaskConfig" in texts["src/config/model_configs.py"],
                f'"{task}": {{' in texts["src/config/default_model_config.py"],
                f'"{task}": "replyer"' in texts["src/llm_models/utils_model.py"],
            )
        )
    present = sum(checks)
    total = len(checks)
    if version not in {BASE_MODEL_CONFIG_VERSION, PATCHED_MODEL_CONFIG_VERSION}:
        return Inspection("unknown", version, (f"仅支持模型配置版本 {BASE_MODEL_CONFIG_VERSION}",))
    if present == 0 and version == BASE_MODEL_CONFIG_VERSION:
        return Inspection("pristine", version, ("可安全应用增强路由",))
    if present == total and version == PATCHED_MODEL_CONFIG_VERSION:
        return Inspection("installed", version, ("四任务路由已完整安装",))
    return Inspection(
        "partial",
        version,
        (f"检测到部分安装或不一致状态：{present}/{total} 个任务标记", "拒绝自动覆盖，请先恢复干净基线"),
    )


def _newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise InstallError(f"{label} 锚点数量应为 1，实际为 {count}")
    return text.replace(old, new, 1)


def _with_newline(block: str, newline: str) -> str:
    return block.replace("\n", newline)


def build_patched_texts(texts: Dict[str, str]) -> Dict[str, str]:
    patched = dict(texts)

    model_key = "src/config/model_configs.py"
    nl = _newline(patched[model_key])
    expression_anchor = _with_newline(
        '''    expression_use: TaskConfig = Field(
        default_factory=TaskConfig,
        json_schema_extra={
            "x-widget": "custom",
            "x-icon": "message-circle-more",
            "advanced": True,
        },
    )
    """表达方式使用模型配置；留空时用 utils 模型"""
''',
        nl,
    )
    patched[model_key] = _replace_once(
        patched[model_key],
        expression_anchor,
        expression_anchor + nl + _with_newline(MODEL_CONFIG_FIELD_BLOCK, nl),
        "model_configs.py",
    )

    default_key = "src/config/default_model_config.py"
    nl = _newline(patched[default_key])
    default_anchor = _with_newline(
        '    "expression_use": {"model_list": [], "max_tokens": 1024, "temperature": 0.3, "hard_timeout": 120.0},\n',
        nl,
    )
    patched[default_key] = _replace_once(
        patched[default_key],
        default_anchor,
        default_anchor + _with_newline(DEFAULT_TASK_BLOCK, nl),
        "default_model_config.py",
    )

    fallback_key = "src/llm_models/utils_model.py"
    nl = _newline(patched[fallback_key])
    fallback_anchor = _with_newline('    "mid_memory": "planner",\n', nl)
    patched[fallback_key] = _replace_once(
        patched[fallback_key],
        fallback_anchor,
        fallback_anchor + _with_newline(FALLBACK_TASK_BLOCK, nl),
        "utils_model.py",
    )

    config_key = "src/config/config.py"
    patched[config_key] = _replace_once(
        patched[config_key],
        f'MODEL_CONFIG_VERSION: str = "{BASE_MODEL_CONFIG_VERSION}"',
        f'MODEL_CONFIG_VERSION: str = "{PATCHED_MODEL_CONFIG_VERSION}"',
        "config.py",
    )
    return patched


def _atomic_write(path: Path, data: bytes) -> None:
    temp = path.with_name(f".{path.name}.khiqwq-routing-{os.getpid()}.tmp")
    try:
        temp.write_bytes(data)
        os.chmod(temp, path.stat().st_mode)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _create_backup(root: Path, before: Dict[str, bytes], after: Dict[str, bytes]) -> Path:
    base = root / ".khiqwq_daily_analysis_backups"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = base / stamp
    suffix = 1
    while backup.exists():
        backup = base / f"{stamp}-{suffix}"
        suffix += 1
    backup.mkdir(parents=True)
    files = {}
    for relative, data in before.items():
        destination = backup / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        files[relative] = {
            "before_sha256": _sha256(data),
            "after_sha256": _sha256(after[relative]),
        }
    manifest = {
        "schema": 1,
        "maibot_root": str(root.resolve()),
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_model_config_version": BASE_MODEL_CONFIG_VERSION,
        "target_model_config_version": PATCHED_MODEL_CONFIG_VERSION,
        "files": files,
    }
    (backup / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return backup


def apply(root: Path) -> Path | None:
    state = inspect(root)
    if state.state == "installed":
        print("状态：已安装，无需重复修改")
        return None
    if state.state != "pristine":
        raise InstallError("当前状态不是受支持的干净基线：" + "；".join(state.details))

    paths, texts, before = _read_texts(root)
    patched_texts = build_patched_texts(texts)
    after = {relative: text.encode("utf-8") for relative, text in patched_texts.items()}
    backup = _create_backup(root.resolve(), before, after)
    changed = []
    try:
        for relative in TARGET_FILES:
            _atomic_write(paths[relative], after[relative])
            changed.append(relative)
        final = inspect(root)
        if final.state != "installed":
            raise InstallError("写入后完整性检查失败")
    except Exception:
        for relative in changed:
            _atomic_write(paths[relative], before[relative])
        raise
    print("状态：应用成功")
    print(f"BACKUP_DIR={backup}")
    return backup


def rollback(root: Path, backup: Path) -> None:
    backup = backup.resolve()
    manifest_path = backup / "manifest.json"
    if not manifest_path.is_file():
        raise InstallError(f"备份缺少 manifest.json：{backup}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if Path(manifest.get("maibot_root", "")).resolve() != root.resolve():
        raise InstallError("备份不属于当前 MaiBot 根目录")
    files = manifest.get("files", {})
    if set(files) != set(TARGET_FILES):
        raise InstallError("备份文件清单不完整")

    paths = _target_paths(root)
    restore_data: Dict[str, bytes] = {}
    for relative in TARGET_FILES:
        current = paths[relative].read_bytes()
        expected_after = files[relative].get("after_sha256")
        if _sha256(current) != expected_after:
            raise InstallError(f"当前文件已在安装后继续变化，拒绝覆盖：{relative}")
        saved = (backup / relative).read_bytes()
        if _sha256(saved) != files[relative].get("before_sha256"):
            raise InstallError(f"备份校验失败：{relative}")
        restore_data[relative] = saved

    for relative in TARGET_FILES:
        _atomic_write(paths[relative], restore_data[relative])
    final = inspect(root)
    if final.state != "pristine":
        raise InstallError("回滚后状态校验失败")
    print("状态：回滚成功")


def _parser() -> ArgumentParser:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("maibot_root", type=Path, help="MaiBot 源码根目录")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="只检测，不写入文件")
    action.add_argument("--apply", action="store_true", help="备份后应用四任务路由")
    action.add_argument("--rollback", type=Path, metavar="BACKUP_DIR", help="使用指定备份回滚")
    return parser


def main(argv: list[str] | None = None) -> int:
    args: Namespace = _parser().parse_args(argv)
    root = args.maibot_root.resolve()
    try:
        if args.check:
            result = inspect(root)
            print(f"状态：{result.state}")
            print(f"MODEL_CONFIG_VERSION={result.version}")
            for detail in result.details:
                print(f"- {detail}")
            return 0 if result.state in {"pristine", "installed"} else 2
        if args.apply:
            apply(root)
            return 0
        rollback(root, args.rollback)
        return 0
    except (InstallError, OSError, json.JSONDecodeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
