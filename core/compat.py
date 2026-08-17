"""Runtime capability checks for the MaiBot plugin host."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class CapabilityReport:
    """Result of checking the host capabilities needed by the plugin."""

    missing_required: tuple[str, ...] = ()
    missing_optional: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.missing_required


def _resolve(root: Any, path: str) -> Any:
    value = root
    for part in path.split("."):
        try:
            value = getattr(value, part)
        except AttributeError:
            return None
        if value is None:
            return None
    return value


def _missing(root: Any, paths: Iterable[str]) -> tuple[str, ...]:
    return tuple(path for path in paths if not callable(_resolve(root, path)))


def inspect_runtime(ctx: Any, *, inject_memory: bool = False) -> CapabilityReport:
    """Check the capability surface used by the current plugin."""

    required = (
        "llm.generate",
        "llm.get_available_models",
        "message.get_by_time_in_chat",
        "chat.get_group_streams",
        "chat.open_session",
        "send.text",
        "send.image",
        "render.html2png",
    )
    optional = ("maisaka.context.append",) if inject_memory else ()
    return CapabilityReport(
        missing_required=_missing(ctx, required),
        missing_optional=_missing(ctx, optional),
    )


def format_failure(report: CapabilityReport) -> str:
    """Return a user-facing explanation for a failed capability check."""

    missing = ", ".join(report.missing_required)
    return (
        "当前 MaiBot/maibot_sdk 运行环境缺少日报插件需要的能力："
        f"{missing}。请升级到插件说明中支持的版本。"
    )
