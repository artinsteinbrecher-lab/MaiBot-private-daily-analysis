"""群聊事件日报的纯数据工具。

本模块不依赖 MaiBot SDK，便于在 Ubuntu、Windows 和 CI 环境中直接测试。
插件运行时能力（消息查询、LLM、渲染、发送）仍由 ``plugin.py`` 通过官方
``ctx.*`` 接口调用。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Sequence, Tuple


_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_GROUP_RE = re.compile(r"^\d{5,15}$")
_URL_RE = re.compile(r"https?://[^\s<>'\"]+")


def parse_summary_command(args: str) -> Tuple[str, str]:
    """解析 ``/summary`` 参数并返回 ``(群号或全部, 今天或昨天)``。

    支持：
    - ``/summary 123456``
    - ``/summary 123456 今天``
    - ``/summary 123456 昨天``
    - ``/summary 全部 今天``
    """

    tokens = [token.strip() for token in (args or "").split() if token.strip()]
    if not tokens:
        raise ValueError("请指定群号或“全部”")
    if len(tokens) > 2:
        raise ValueError("格式：/summary 群号|全部 [今天|昨天]")

    target = tokens[0]
    if target.lower() == "all":
        target = "全部"
    if target != "全部" and not _GROUP_RE.fullmatch(target):
        raise ValueError("群号必须是 5—15 位数字，或填写“全部”")

    period = tokens[1] if len(tokens) == 2 else "今天"
    if period not in {"今天", "昨天"}:
        raise ValueError("时间范围只支持“今天”或“昨天”")
    return target, period


def is_valid_time(value: Any) -> bool:
    return isinstance(value, str) and bool(_TIME_RE.fullmatch(value.strip()))


def _clean_text(value: Any, max_length: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:max_length]


def _clean_string_list(value: Any, max_items: int, max_length: int) -> List[str]:
    if not isinstance(value, list):
        return []
    result: List[str] = []
    seen = set()
    for item in value:
        text = _clean_text(item, max_length)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= max_items:
            break
    return result


def _normalize_anchor(value: Any) -> Dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    time_value = _clean_text(value.get("time"), 5)
    speaker = _clean_text(value.get("speaker"), 40)
    quote = _clean_text(value.get("quote"), 180)
    if not is_valid_time(time_value) or not speaker or not quote:
        return None
    return {"time": time_value, "speaker": speaker, "quote": quote}


def _normalize_importance(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"minor", "动态", "其他", "普通", "次要"}:
        return "minor"
    return "major"


def partition_events(events: Iterable[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """按重要程度拆分事件，同时保持各自的时间顺序。"""

    major: List[Dict[str, Any]] = []
    minor: List[Dict[str, Any]] = []
    for event in events:
        (minor if event.get("importance") == "minor" else major).append(event)
    return major, minor


def normalize_event_report(
    value: Any,
    *,
    max_events: int = 8,
    max_minor_events: int = 0,
    max_anchors: int = 2,
    include_links: bool = True,
) -> Dict[str, Any]:
    """校验并清洗 LLM 返回的事件日报 JSON。

    时间字段必须是合法的 ``HH:MM``。无效事件直接丢弃，避免把模型幻觉的时间
    带进最终日报。结果按开始时间排序，并对完全相同的标题和时间段去重。
    """

    if not isinstance(value, dict):
        value = {}
    overview = _clean_text(value.get("overview"), 700)
    raw_events = value.get("events")
    if not isinstance(raw_events, list):
        raw_events = []

    events: List[Dict[str, Any]] = []
    seen = set()
    for raw in raw_events:
        if not isinstance(raw, dict):
            continue

        anchors: List[Dict[str, str]] = []
        raw_anchors = raw.get("anchors")
        if max_anchors > 0 and isinstance(raw_anchors, list):
            for item in raw_anchors:
                anchor = _normalize_anchor(item)
                if anchor is not None:
                    anchors.append(anchor)
                if len(anchors) >= max_anchors:
                    break

        start_time = _clean_text(raw.get("start_time"), 5)
        end_time = _clean_text(raw.get("end_time"), 5)
        if not is_valid_time(start_time) and anchors:
            start_time = anchors[0]["time"]
        if not is_valid_time(end_time) and anchors:
            end_time = anchors[-1]["time"]
        if not is_valid_time(start_time) or not is_valid_time(end_time):
            continue
        if end_time < start_time:
            start_time, end_time = end_time, start_time

        title = _clean_text(raw.get("title"), 60)
        summary = _clean_text(raw.get("summary"), 500)
        if not title or not summary:
            continue

        key = (start_time, end_time, title)
        if key in seen:
            continue
        seen.add(key)

        outcomes = _clean_string_list(raw.get("outcomes"), 6, 180)
        pending = _clean_string_list(raw.get("pending"), 6, 180)
        participants = _clean_string_list(raw.get("participants"), 8, 40)

        links: List[str] = []
        if include_links:
            raw_links = _clean_string_list(raw.get("links"), 8, 500)
            for link in raw_links:
                match = _URL_RE.search(link)
                if match:
                    links.append(match.group(0).rstrip(".,;，。；"))

        events.append(
            {
                "importance": _normalize_importance(raw.get("importance")),
                "start_time": start_time,
                "end_time": end_time,
                "title": title,
                "summary": summary,
                "outcomes": outcomes,
                "pending": pending,
                "participants": participants,
                "anchors": anchors,
                "links": links,
            }
        )

    events.sort(key=lambda item: (item["start_time"], item["end_time"], item["title"]))
    major, minor = partition_events(events)
    major_limit = max(1, int(max_events or 8))
    minor_limit = max(0, int(max_minor_events or 0))
    selected = major[:major_limit] + minor[:minor_limit]
    selected.sort(key=lambda item: (item["start_time"], item["end_time"], item["title"]))
    return {"overview": overview, "events": selected}


def merge_event_reports_fallback(
    reports: Iterable[Dict[str, Any]],
    *,
    max_events: int,
    max_minor_events: int = 0,
    max_anchors: int,
    include_links: bool,
) -> Dict[str, Any]:
    """在合并 LLM 失败时做确定性的保底合并。"""

    candidates: List[Dict[str, Any]] = []
    overview_parts: List[str] = []
    for report in reports:
        normalized = normalize_event_report(
            report,
            max_events=max_events * 4,
            max_minor_events=max_minor_events * 4,
            max_anchors=max_anchors,
            include_links=include_links,
        )
        if normalized["overview"]:
            overview_parts.append(normalized["overview"])
        candidates.extend(normalized["events"])

    merged = normalize_event_report(
        {"overview": "；".join(overview_parts), "events": candidates},
        max_events=max_events,
        max_minor_events=max_minor_events,
        max_anchors=max_anchors,
        include_links=include_links,
    )
    return merged


def build_event_plain_text(
    group_name: str,
    group_id: str,
    report_date: datetime,
    period_text: str,
    report: Dict[str, Any],
) -> str:
    """把结构化事件日报转换为适合日志、记忆注入和文本降级的内容。"""

    lines = [
        f"【{group_name}（{group_id}）】{report_date:%Y-%m-%d} 群聊事件日报",
        f"统计范围：{period_text}",
    ]
    overview = str(report.get("overview") or "").strip()
    if overview:
        lines.extend(["", f"概览：{overview}"])

    major_events, minor_events = partition_events(report.get("events") or [])
    sections = (("主要事件", major_events), ("其他动态", minor_events))
    for section_title, section_events in sections:
        if not section_events:
            continue
        lines.extend(["", f"【{section_title}】"])
        for index, event in enumerate(section_events, start=1):
            lines.extend(
                [
                    "",
                    f"{index}. {event['start_time']}—{event['end_time']}｜{event['title']}",
                    event["summary"],
                ]
            )
            outcomes = event.get("outcomes") or []
            if outcomes:
                lines.append("结论：" + "；".join(outcomes))
            pending = event.get("pending") or []
            if pending:
                lines.append("待确认：" + "；".join(pending))
            for anchor in event.get("anchors") or []:
                lines.append(f"回查：{anchor['time']} {anchor['speaker']}：{anchor['quote']}")
            links = event.get("links") or []
            if links:
                lines.append("链接：" + " ".join(links))

    coverage = report.get("coverage") or {}
    if coverage:
        lines.extend(
            [
                "",
                "【完整度】"
                f"原始 {int(coverage.get('total_messages') or 0)} 条，"
                f"实际分析 {int(coverage.get('analyzed_messages') or 0)} 条，"
                f"覆盖率 {float(coverage.get('coverage_percent') or 0):.1f}%。",
            ]
        )
        failed_ranges = coverage.get("failed_ranges") or []
        if failed_ranges:
            ranges = "、".join(
                f"{item.get('start_time', '?')}—{item.get('end_time', '?')}"
                for item in failed_ranges
            )
            lines.append("未完成时段：" + ranges)
    return "\n".join(lines)


def build_daily_index_text(
    report_date: datetime,
    period_text: str,
    group_results: Sequence[Dict[str, Any]],
) -> str:
    """生成可在 QQ 中搜索的纯文本日报总目录。"""

    with_events = [
        item
        for item in group_results
        if item.get("status") in {"ok", "partial"}
        and item.get("report", {}).get("events")
    ]
    lines = [
        f"【{report_date:%Y-%m-%d} 群聊事件日报】",
        f"统计范围：{period_text}",
        f"共检查 {len(group_results)} 个群，{len(with_events)} 个群有重要事件。",
        "",
    ]

    for index, item in enumerate(group_results, start=1):
        group_name = item.get("group_name") or f"群{item.get('group_id', '')}"
        group_id = item.get("group_id") or ""
        status = item.get("status")
        if status in {"ok", "partial"}:
            events = item.get("report", {}).get("events") or []
            major, minor = partition_events(events)
            topic_items = [
                f"{event['start_time']} {event['title']}" for event in major
            ]
            topic_items.extend(
                f"{event['start_time']} {event['title']}" for event in minor[:10]
            )
            if len(minor) > 10:
                topic_items.append(f"另有 {len(minor) - 10} 条其他动态")
            prefix = "部分完成；" if status == "partial" else ""
            lines.append(
                f"{index}. {group_name}（{group_id}）：{prefix}"
                + "；".join(topic_items)
            )
        elif status == "insufficient":
            lines.append(f"{index}. {group_name}（{group_id}）：消息不足，未生成")
        elif status == "empty":
            lines.append(f"{index}. {group_name}（{group_id}）：无重要事件")
        else:
            lines.append(f"{index}. {group_name}（{group_id}）：生成失败")
    return "\n".join(lines).strip()


def split_message_text(text: str, limit: int = 1500) -> List[str]:
    """按行拆分 QQ 文本，避免单条消息过长。"""

    if len(text) <= limit:
        return [text]
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0
    for line in text.splitlines():
        line_len = len(line) + 1
        if current and current_len + line_len > limit:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        if len(line) > limit:
            if current:
                chunks.append("\n".join(current))
                current = []
                current_len = 0
            for start in range(0, len(line), limit):
                chunks.append(line[start : start + limit])
            continue
        current.append(line)
        current_len += line_len
    if current:
        chunks.append("\n".join(current))
    return [chunk for chunk in chunks if chunk]
