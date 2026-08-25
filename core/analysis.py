"""
聊天分析服务

把聊天记录交给 LLM 做事实型事件日报和个人画像分析。
所有 LLM 调用通过宿主注入的 ``ctx.llm`` 能力完成；纯数据处理保持为静态方法。

消息字典遵循插件运行时的扁平结构（由 plugin.py 的归一化层提供）：
    {
        "user_id": str, "user_nickname": str, "user_cardname": str,
        "processed_plain_text": str, "time": float,
        "is_command": bool, "is_notify": bool,
    }
"""

import re
import json
import asyncio
import hashlib
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from collections import Counter
from zoneinfo import ZoneInfo

from .event_digest import (
    merge_adjacent_event_candidates,
    merge_event_reports_fallback,
    normalize_event_report,
    partition_events,
)


# LLM 各任务的输出 token 上限。
# 通过 maibot_sdk 的 ``rpc_timeout_ms`` 参数把插件配置的等待时间传递到宿主，避免
# 高质量模型仍被 cap.call 的默认 30 秒 RPC 上限提前切断；输出长度仍保持保守。
# Keep the first extraction pass deliberately compact.  DSV4F exposes its
# reasoning separately, but the task-level output budget is shared with the
# final JSON.  Smaller chunks leave enough room for a complete structured
# response and make fallback/retry decisions deterministic.
_EVENT_CHUNK_MESSAGES = 80
_EVENT_CHUNK_CHARACTERS = 6000
_TOPIC_MAX_CANDIDATES_PER_CHUNK = 12
_MAJOR_REFINE_BATCH_SIZE = 4
_EVENT_CANDIDATE_HARD_LIMIT = 1000

# 默认并发 LLM 调用上限。设为 2：兼顾速度与"上游串行时排队不耗尽超时预算"。
# 上游是高延迟聚合渠道（单次请求普遍超过 1 分钟）时，2 路并发跑不完高流量群的
# 全部分片；可通过插件配置调高，上限见 _LLM_CONCURRENCY_MAX。
_LLM_MAX_CONCURRENCY = 2
_LLM_CONCURRENCY_MAX = 6

# 单次 LLM 调用的最长等待（秒），到点放弃该次分析项。默认值偏保守；宿主各任务的
# 硬超时因部署而异（实测 240-360 秒），插件等待低于宿主硬超时会放弃即将成功的慢
# 响应，建议按宿主实际配置调高（见插件"高级"配置）。
_DEFAULT_CALL_TIMEOUT_S = 180
_RPC_TIMEOUT_GRACE_S = 5

# 默认跟随 MaiBot replyer；仅当宿主实际注册专用任务时，插件加载过程才传入专用路由。
_DEFAULT_MODEL_TASK = "replyer"


class AnalysisService:
    """聊天记录分析服务（绑定插件 ctx，统一走 ctx.llm / ctx.logger）"""

    # Emoji 正则（精确匹配，避免误伤中文字符）
    EMOJI_PATTERN = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # emoticons
        "\U0001F300-\U0001F5FF"  # symbols & pictographs
        "\U0001F680-\U0001F6FF"  # transport & map symbols
        "\U0001F1E0-\U0001F1FF"  # flags
        "\U00002702-\U000027B0"  # dingbats
        "\U0001F900-\U0001F9FF"  # supplemental symbols
        "\U0001FA00-\U0001FA6F"  # chess symbols
        "\U0001FA70-\U0001FAFF"  # symbols and pictographs extended-A
        "\U00002600-\U000026FF"  # misc symbols
        "\U0000FE00-\U0000FE0F"  # variation selectors
        "\U0001F000-\U0001F02F"  # mahjong tiles
        "\U0001F0A0-\U0001F0FF"  # playing cards
        "]+",
        flags=re.UNICODE,
    )

    def __init__(
        self,
        ctx: Any,
        model: str = _DEFAULT_MODEL_TASK,
        topic_model: str = "",
        refine_model: str = "",
        call_timeout_s: int = _DEFAULT_CALL_TIMEOUT_S,
        timezone_name: str = "Asia/Shanghai",
        verify_model: str = "",
        user_profile_model: str = "",
        llm_concurrency: int = _LLM_MAX_CONCURRENCY,
    ):
        self.ctx = ctx
        self.logger = ctx.logger
        # 模型任务名由插件从宿主实际注册的高级任务解析；直接使用时默认跟随 replyer。
        self.model = model or _DEFAULT_MODEL_TASK
        self.topic_model = topic_model or self.model
        self.refine_model = refine_model or self.topic_model
        self.verify_model = verify_model or ""
        self.user_profile_model = user_profile_model or self.model
        # 单次 LLM 调用的客户端等待上限（秒），可由插件配置覆盖
        self.call_timeout_s = max(5, int(call_timeout_s or _DEFAULT_CALL_TIMEOUT_S))
        self.timezone_name = timezone_name or "Asia/Shanghai"
        # 限制并发 LLM 调用数：若上游串行处理，一次放出过多调用会让排队靠后的调用
        # 把等待时间算进自己的超时预算。信号量在真正发起 ctx.llm.generate 前获取，
        # 让每个调用的 RPC 预算从有空闲槽位时才开始。
        self.llm_concurrency = self._clamp_concurrency(llm_concurrency)
        self._llm_semaphore = asyncio.Semaphore(self.llm_concurrency)

    @staticmethod
    def _clamp_concurrency(value: int) -> int:
        return max(1, min(_LLM_CONCURRENCY_MAX, int(value or _LLM_MAX_CONCURRENCY)))

    def set_llm_concurrency(self, value: int) -> None:
        """热更新并发上限。在途调用继续持有旧信号量直到结束，属良性不一致。"""
        clamped = self._clamp_concurrency(value)
        if clamped == self.llm_concurrency:
            return
        self.llm_concurrency = clamped
        self._llm_semaphore = asyncio.Semaphore(clamped)

    # ==================== LLM 调用封装 ====================

    async def _llm(
        self,
        prompt: str,
        *,
        request_type: str,
        max_tokens: Optional[int] = None,
        temperature: float = 0.7,
        model_task: str = "",
    ) -> Optional[str]:
        """调用宿主 LLM 能力，成功返回文本，失败返回 None"""
        try:
            async with self._llm_semaphore:
                generate_kwargs = {
                    "model": model_task or self.model,
                    "temperature": temperature,
                    "rpc_timeout_ms": self.call_timeout_s * 1000,
                }
                # The four plugin-specific MaiBot tasks own their output limits.
                # Do not pass a plugin-wide cap here: an explicit value would
                # override the task's configured max_tokens and truncate long
                # extraction JSON before the parser can see it.
                if max_tokens is not None:
                    generate_kwargs["max_tokens"] = max(1, int(max_tokens))
                result = await asyncio.wait_for(
                    self.ctx.llm.generate(prompt, **generate_kwargs),
                    timeout=self.call_timeout_s + _RPC_TIMEOUT_GRACE_S,
                )
        except asyncio.TimeoutError:
            self.logger.warning(f"LLM 调用超时 ({request_type}, >{self.call_timeout_s}s)")
            return None
        except Exception as e:
            self.logger.error(f"LLM 调用异常 ({request_type}): {e}", exc_info=True)
            return None

        if not isinstance(result, dict) or not result.get("success", False):
            err = result.get("error") if isinstance(result, dict) else result
            self.logger.error(f"LLM 生成失败 ({request_type}): {err}")
            return None

        response = result.get("response")
        if not response:
            self.logger.error(f"LLM 返回空内容 ({request_type})")
            return None
        return str(response)

    # ==================== 纯数据处理（静态） ====================

    @staticmethod
    def format_messages(messages: List[dict]) -> str:
        """格式化聊天记录为文本"""
        formatted = []
        for msg in messages:
            timestamp = msg.get("time", 0)
            time_str = datetime.fromtimestamp(timestamp).strftime("%H:%M:%S")
            nickname = msg.get("user_nickname", "未知用户")
            cardname = msg.get("user_cardname", "")
            display_name = cardname if cardname else nickname
            text = msg.get("processed_plain_text") or ""

            if text:
                formatted.append(f"[{time_str}] {display_name}: {text}")

        return "\n".join(formatted)

    @classmethod
    def count_emojis(cls, text: str) -> int:
        """统计文本中的 emoji 数量"""
        return len(cls.EMOJI_PATTERN.findall(text))

    @classmethod
    def analyze_user_stats(cls, messages: List[dict]) -> Dict[str, Dict]:
        """分析用户统计数据

        Returns:
            {user_id: {user_id, nickname, message_count, char_count, emoji_count, hours}}
        """
        user_stats: Dict[str, Dict] = {}

        for msg in messages:
            user_id = str(msg.get("user_id", ""))
            if not user_id:
                continue

            nickname = msg.get("user_nickname", "未知用户")
            text = msg.get("processed_plain_text") or ""

            if user_id not in user_stats:
                user_stats[user_id] = {
                    "user_id": user_id,
                    "nickname": nickname,
                    "message_count": 0,
                    "char_count": 0,
                    "emoji_count": 0,
                    "hours": Counter(),
                }

            stats = user_stats[user_id]
            stats["message_count"] += 1
            stats["char_count"] += len(text)
            stats["emoji_count"] += cls.count_emojis(text)

            timestamp = msg.get("time", 0)
            hour = datetime.fromtimestamp(timestamp).hour
            stats["hours"][hour] += 1

        return user_stats

    @staticmethod
    def filter_user_messages(messages: List[dict], user_id: str) -> List[dict]:
        """过滤出指定用户的消息"""
        user_id_str = str(user_id)
        return [msg for msg in messages if str(msg.get("user_id", "")) == user_id_str]

    @classmethod
    def analyze_single_user_stats(cls, messages: List[dict]) -> Dict:
        """分析单个用户的统计数据（只使用该用户的消息）"""
        if not messages:
            return {
                "message_count": 0,
                "char_count": 0,
                "emoji_count": 0,
                "hours": Counter(),
                "hourly_distribution": {},
            }

        message_count = 0
        char_count = 0
        emoji_count = 0
        hours = Counter()

        for msg in messages:
            text = msg.get("processed_plain_text") or ""
            message_count += 1
            char_count += len(text)
            emoji_count += cls.count_emojis(text)

            timestamp = msg.get("time", 0)
            hour = datetime.fromtimestamp(timestamp).hour
            hours[hour] += 1

        hourly_distribution = {h: hours.get(h, 0) for h in range(24)}

        return {
            "message_count": message_count,
            "char_count": char_count,
            "emoji_count": emoji_count,
            "hours": hours,
            "hourly_distribution": hourly_distribution,
        }

    # ==================== 群聊事件日报（LLM） ====================

    def _select_time_balanced_messages(
        self, messages: List[dict], limit: int
    ) -> List[dict]:
        """按配置时区的小时分桶均衡抽样，避免高峰聊天淹没其他时段。"""

        if limit <= 0 or len(messages) <= limit:
            return list(messages)
        if limit == 1:
            return [messages[-1]]

        buckets: Dict[int, List[dict]] = {}
        for message in messages:
            timestamp = float(message.get("time") or 0)
            hour = self._event_datetime(timestamp).hour if timestamp > 0 else -1
            buckets.setdefault(hour, []).append(message)

        bucket_items = sorted(buckets.items(), key=lambda item: item[0])
        if len(bucket_items) > limit:
            last_index = len(bucket_items) - 1
            chosen_indexes = {
                round(position * last_index / (limit - 1))
                for position in range(limit)
            }
            bucket_items = [
                item for index, item in enumerate(bucket_items) if index in chosen_indexes
            ]

        bucket_count = len(bucket_items)
        quotas = [limit // bucket_count] * bucket_count
        for index in range(limit % bucket_count):
            quotas[index] += 1
        quotas = [
            min(quota, len(bucket))
            for quota, (_, bucket) in zip(quotas, bucket_items)
        ]

        remaining = limit - sum(quotas)
        while remaining > 0:
            progressed = False
            for index, (_, bucket) in enumerate(bucket_items):
                if quotas[index] >= len(bucket):
                    continue
                quotas[index] += 1
                remaining -= 1
                progressed = True
                if remaining == 0:
                    break
            if not progressed:
                break

        selected: List[dict] = []
        for quota, (_, bucket) in zip(quotas, bucket_items):
            if quota <= 0:
                continue
            if quota >= len(bucket):
                selected.extend(bucket)
                continue
            if quota == 1:
                selected.append(bucket[len(bucket) // 2])
                continue
            last_index = len(bucket) - 1
            selected.extend(
                bucket[round(position * last_index / (quota - 1))]
                for position in range(quota)
            )
        selected.sort(key=lambda message: float(message.get("time") or 0))
        return selected

    @staticmethod
    def _chunk_event_messages(
        messages: List[dict],
        *,
        max_messages: int = _EVENT_CHUNK_MESSAGES,
        max_characters: int = _EVENT_CHUNK_CHARACTERS,
    ) -> List[List[dict]]:
        """按消息数和字符数切分，控制单次 LLM 请求体积。"""

        max_messages = max(20, min(300, int(max_messages or _EVENT_CHUNK_MESSAGES)))
        max_characters = max(
            2000,
            min(30000, int(max_characters or _EVENT_CHUNK_CHARACTERS)),
        )
        chunks: List[List[dict]] = []
        current: List[dict] = []
        current_chars = 0
        for message in messages:
            text = str(message.get("processed_plain_text") or "")
            estimated = len(text) + 60
            if current and (
                len(current) >= max_messages
                or current_chars + estimated > max_characters
            ):
                chunks.append(current)
                current = []
                current_chars = 0
            current.append(message)
            current_chars += estimated
        if current:
            chunks.append(current)
        return chunks

    def _event_datetime(self, timestamp: float) -> datetime:
        """按插件配置时区格式化事件时间，避免 Ubuntu 容器的系统时区影响日报。"""

        try:
            return datetime.fromtimestamp(timestamp, ZoneInfo(self.timezone_name))
        except Exception:
            return datetime.fromtimestamp(timestamp)

    @staticmethod
    def _event_speaker(message: dict) -> str:
        return str(
            message.get("user_cardname")
            or message.get("user_nickname")
            or "未知用户"
        ).strip()

    def _event_message_id(self, message: dict) -> str:
        """为消息生成跨格式化/校验阶段一致的本地证据 ID。"""

        configured = str(message.get("message_id") or message.get("id") or "").strip()
        if configured:
            return configured[:120]
        timestamp = float(message.get("time") or 0)
        text = re.sub(
            r"\s+", " ", str(message.get("processed_plain_text") or "")
        ).strip()
        payload = f"{timestamp:.6f}\0{self._event_speaker(message)}\0{text}".encode(
            "utf-8", errors="ignore"
        )
        return "m-" + hashlib.sha1(payload).hexdigest()[:16]

    def _format_event_messages(self, messages: List[dict]) -> str:
        """为事件抽取提供带精确时间和发言人的记录。"""

        lines: List[str] = []
        for message in messages:
            text = re.sub(
                r"\s+",
                " ",
                str(message.get("processed_plain_text") or ""),
            ).strip()
            if len(text) <= 1 or text.startswith("/"):
                continue
            timestamp = float(message.get("time") or 0)
            if timestamp <= 0:
                continue
            time_text = self._event_datetime(timestamp).strftime("%H:%M:%S")
            display_name = self._event_speaker(message)
            message_id = self._event_message_id(message)
            lines.append(f"[{time_text}] [id={message_id}] {display_name}: {text[:800]}")
        return "\n".join(lines)

    def _ground_event_report(
        self,
        value: Any,
        messages: List[dict],
        *,
        max_events: int,
        max_minor_events: int = 0,
        max_anchors: int,
        include_links: bool,
    ) -> Dict[str, Any]:
        """把模型给出的时间、原话、参与者和链接约束到真实输入消息。"""

        if not isinstance(value, dict):
            value = {}

        records: List[Dict[str, str]] = []
        valid_minutes = set()
        valid_speakers = set()
        source_text = []
        for message in messages:
            text = re.sub(
                r"\s+",
                " ",
                str(message.get("processed_plain_text") or ""),
            ).strip()
            timestamp = float(message.get("time") or 0)
            if not text or timestamp <= 0:
                continue
            minute = self._event_datetime(timestamp).strftime("%H:%M")
            speaker = self._event_speaker(message)
            message_id = self._event_message_id(message)
            records.append({
                "message_id": message_id,
                "time": minute,
                "speaker": speaker,
                "text": text,
            })
            valid_minutes.add(minute)
            valid_speakers.add(speaker)
            source_text.append(text)

        grounded_events: List[Dict[str, Any]] = []
        valid_record_ids = {record["message_id"] for record in records}
        for raw_event in value.get("events") or []:
            if not isinstance(raw_event, dict):
                continue
            event = dict(raw_event)

            anchors = []
            evidence_ids = []
            if max_anchors > 0:
                for raw_anchor in event.get("anchors") or []:
                    if not isinstance(raw_anchor, dict):
                        continue
                    anchor_time = str(raw_anchor.get("time") or "").strip()
                    anchor_speaker = str(raw_anchor.get("speaker") or "").strip()
                    anchor_quote = re.sub(
                        r"\s+",
                        " ",
                        str(raw_anchor.get("quote") or ""),
                    ).strip()
                    if not anchor_time or not anchor_speaker or not anchor_quote:
                        continue
                    matching_records = [
                        record
                        for record in records
                        if record["time"] == anchor_time
                        and record["speaker"] == anchor_speaker
                        and anchor_quote in record["text"]
                    ]
                    if matching_records:
                        anchors.append(
                            {
                                "time": anchor_time,
                                "speaker": anchor_speaker,
                                "quote": anchor_quote,
                            }
                        )
                        evidence_ids.append(matching_records[0]["message_id"])
                    if len(anchors) >= max_anchors:
                        break
            event["anchors"] = anchors
            configured_evidence = event.get("evidence_ids")
            if isinstance(configured_evidence, list):
                evidence_ids.extend(
                    evidence_id
                    for item in configured_evidence
                    if (evidence_id := str(item).strip()) in valid_record_ids
                )
            event["evidence_ids"] = list(dict.fromkeys(evidence_ids))[:24]

            start_time = str(event.get("start_time") or "").strip()
            end_time = str(event.get("end_time") or "").strip()
            if start_time not in valid_minutes:
                start_time = anchors[0]["time"] if anchors else ""
            if end_time not in valid_minutes:
                end_time = anchors[-1]["time"] if anchors else ""
            event["start_time"] = start_time
            event["end_time"] = end_time

            raw_participants = event.get("participants")
            if not isinstance(raw_participants, list):
                raw_participants = []
            event["participants"] = [
                speaker
                for speaker in raw_participants
                if str(speaker).strip() in valid_speakers
            ]
            if include_links:
                raw_links = event.get("links")
                if not isinstance(raw_links, list):
                    raw_links = []
                event["links"] = [
                    link
                    for link in raw_links
                    if str(link).strip()
                    and any(str(link).strip() in text for text in source_text)
                ]
            else:
                event["links"] = []

            def bind_claims(values: Any) -> tuple[list, list]:
                if not isinstance(values, list):
                    return [], []
                kept = []
                bindings = []
                seen = set()
                for value in values:
                    claim_value = value.get("claim") if isinstance(value, dict) else value
                    claim = str(claim_value or "").strip()
                    claim = re.sub(r"\s+", " ", claim)[:240]
                    if not claim or claim in seen:
                        continue
                    compact_claim = re.sub(r"\W", "", claim).lower()
                    configured_claim_ids = (
                        value.get("evidence_ids") if isinstance(value, dict) else []
                    )
                    claim_ids = []
                    configured_ids = {
                        str(item).strip()
                        for item in configured_claim_ids or []
                        if str(item).strip() in valid_record_ids
                    }
                    for record in records:
                        compact_text = re.sub(r"\W", "", record["text"]).lower()
                        text_matches = len(compact_claim) >= 4 and (
                            compact_claim in compact_text or compact_text in compact_claim
                        )
                        if text_matches and (
                            not configured_ids or record["message_id"] in configured_ids
                        ):
                            claim_ids.append(record["message_id"])
                    claim_ids = list(dict.fromkeys(claim_ids))[:8]
                    if not claim_ids:
                        continue
                    seen.add(claim)
                    kept.append(claim)
                    bindings.append({"claim": claim, "evidence_ids": claim_ids})
                return kept, bindings

            event["facts"], event["fact_bindings"] = bind_claims(
                event.get("facts") or [event.get("summary")]
            )
            event["outcomes"], event["outcome_bindings"] = bind_claims(
                event.get("outcomes")
            )
            event["pending"], event["pending_bindings"] = bind_claims(
                event.get("pending")
            )
            if not event["facts"]:
                continue
            event["evidence_ids"] = list(
                dict.fromkeys(
                    event["evidence_ids"]
                    + [
                        evidence_id
                        for field in (
                            "fact_bindings",
                            "outcome_bindings",
                            "pending_bindings",
                        )
                        for binding in event[field]
                        for evidence_id in binding["evidence_ids"]
                    ]
                )
            )[:24]
            grounded_events.append(event)

        return normalize_event_report(
            {"overview": value.get("overview"), "events": grounded_events},
            max_events=max_events,
            max_minor_events=max_minor_events,
            max_anchors=max_anchors,
            include_links=include_links,
        )

    @staticmethod
    def _event_detail_instruction(detail_level: str) -> str:
        if detail_level == "full":
            return (
                "完整模式：除纯寒暄、无意义复读和纯表情刷屏外，分享、求助、测试、"
                "故障、决定、争议、教程、资源发布和有明确内容的普通话题都应记录。"
                "重大决定或持续讨论标为 major，其余有效动态标为 minor。"
            )
        if detail_level == "concise":
            return (
                "精简模式：只记录明确决定、故障、发布、重要通知或持续深入讨论，"
                "全部标为 major；普通话题不记录。"
            )
        return (
            "标准模式：记录重要事件及有实际内容的普通话题；重要事项标为 major，"
            "较小但可回查的有效动态标为 minor。"
        )

    async def _extract_event_chunk(
        self,
        messages: List[dict],
        *,
        max_anchors: int,
        include_links: bool,
        detail_level: str,
        chunk_label: str,
        chunk_count: int,
    ) -> Optional[Dict[str, Any]]:
        chat_text = self._format_event_messages(messages)
        if not chat_text:
            return {"overview": "", "events": []}

        detail_instruction = self._event_detail_instruction(detail_level)
        prompt = f"""你是一名严谨的群聊话题索引员。请从下面的 QQ 群聊记录中建立轻量时间线，
不要做成员活跃度、性格、MBTI、金句或娱乐排名，也不要把纯寒暄、无意义复读、表情刷屏当作话题。

这是全天记录的第 {chunk_label}/{chunk_count} 段。每条记录前的时间是唯一可信时间来源。
{detail_instruction}

群聊记录：
{chat_text}

输出要求：
1. 按时间顺序提取本段最多 {_TOPIC_MAX_CANDIDATES_PER_CHUNK} 个有实际内容的话题。
2. 这是第一阶段索引：summary 只写一两句事实概括，不展开长篇分析。
3. start_time/end_time 和 anchors.time 必须原样取自记录中的 HH:MM，不得猜测。
4. 每个话题最多保留 1 条关键原话；quote 不超过 80 字。
5. 没有有效话题时返回空 events。
6. 只陈述记录能支持的事实，不要自行补全结论。每条事实都必须绑定真实 id，claim 应尽量复用
   对应原消息的连续措辞，便于本地进行严格文本核验。
7. 链接必须逐字复制原文；{"保留重要链接" if include_links else "links 始终返回空数组"}。
8. importance 只能是 major 或 minor。故障、决定、发布、重要通知、争议或持续深入讨论标为 major；
   其他有实际内容的话题标为 minor。

只返回 JSON 对象，不要 Markdown：
{{
  "overview": "本段话题概览，80字以内",
  "events": [
    {{
      "importance": "major",
      "start_time": "09:20",
      "end_time": "10:05",
      "title": "事件标题",
      "summary": "一两句事实概括",
      "status": "discussed",
      "facts": [{{"claim": "可核对的事实", "evidence_ids": ["消息id"]}}],
      "outcomes": [],
      "pending": [],
      "participants": ["与事件直接相关的人"],
      "anchors": [
        {{"time": "09:24", "speaker": "昵称", "quote": "关键原话"}}
      ],
      "links": ["https://example.com"]
    }}
  ]
}}"""
        prompt += (
            "\n\nHard constraints for the extraction pass: include event_id for every event "
            "(it may be empty on this first pass). Represent every fact, outcome, and pending "
            "item as an object with claim and evidence_ids. Omit any claim that cannot be "
            "grounded in the supplied source messages."
        )
        result = await self._llm(
            prompt,
            request_type="plugin.daily_event.extract",
            temperature=0.2,
            model_task=self.topic_model,
        )
        if not result:
            return None
        parsed = self._parse_llm_json_object(result)
        if parsed is None:
            self.logger.warning(f"事件分段 {chunk_label}/{chunk_count} 返回了无效 JSON")
            return None
        return self._ground_event_report(
            parsed,
            messages,
            max_events=_TOPIC_MAX_CANDIDATES_PER_CHUNK,
            max_minor_events=_TOPIC_MAX_CANDIDATES_PER_CHUNK,
            max_anchors=min(1, max_anchors),
            include_links=include_links,
        )

    @staticmethod
    def _time_to_minutes(value: Any) -> int:
        try:
            hour_text, minute_text = str(value).split(":", 1)
            hour, minute = int(hour_text), int(minute_text)
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return hour * 60 + minute
        except Exception:
            pass
        return -1

    def _messages_for_event(
        self,
        event: Dict[str, Any],
        messages: List[dict],
        *,
        padding_minutes: int = 0,
        max_messages: int = 0,
    ) -> List[dict]:
        start = self._time_to_minutes(event.get("start_time"))
        end = self._time_to_minutes(event.get("end_time"))
        if start < 0 or end < 0:
            return []
        start = max(0, start - max(0, padding_minutes))
        end = min(23 * 60 + 59, end + max(0, padding_minutes))
        selected = []
        for message in messages:
            timestamp = float(message.get("time") or 0)
            if timestamp <= 0:
                continue
            moment = self._event_datetime(timestamp)
            minute = moment.hour * 60 + moment.minute
            if start <= minute <= end:
                selected.append(message)
        if max_messages > 0 and len(selected) > max_messages:
            return self._select_time_balanced_messages(selected, max_messages)
        return selected

    def _prepare_event_candidates(
        self,
        report: Dict[str, Any],
        messages: List[dict],
        *,
        max_events: int,
        max_minor_events: int,
        max_anchors: int,
        include_links: bool,
    ) -> Dict[str, Any]:
        """补充源消息指标、保守提升主要事件，并分别限制两类结果。"""

        events = merge_adjacent_event_candidates(
            report.get("events") or [],
            max_anchors=max_anchors,
        )
        for event in events:
            source = self._messages_for_event(event, messages)
            speakers = {
                self._event_speaker(message)
                for message in source
                if self._event_speaker(message)
            }
            event["source_message_count"] = len(source)
            event["source_participant_count"] = len(speakers)
            start = self._time_to_minutes(event.get("start_time"))
            end = self._time_to_minutes(event.get("end_time"))
            duration = max(0, end - start) if start >= 0 and end >= 0 else 0
            if event.get("importance") == "minor":
                should_promote = (
                    len(source) >= 28
                    or (
                        duration >= 20
                        and len(source) >= 12
                        and len(speakers) >= 4
                    )
                    or (bool(event.get("links")) and len(source) >= 16)
                )
                if should_promote:
                    event["importance"] = "major"

        major, minor = partition_events(events)

        def major_score(event: Dict[str, Any]) -> float:
            start = self._time_to_minutes(event.get("start_time"))
            end = self._time_to_minutes(event.get("end_time"))
            duration = max(0, end - start) if start >= 0 and end >= 0 else 0
            return (
                float(event.get("source_message_count") or 0) * 2.0
                + float(event.get("source_participant_count") or 0) * 5.0
                + duration / 4.0
                + len(event.get("links") or []) * 4.0
            )

        major = sorted(major, key=major_score, reverse=True)[:max_events]
        if max_minor_events > 0:
            minor = minor[:max_minor_events]
        selected = major + minor
        selected.sort(
            key=lambda item: (
                str(item.get("start_time") or ""),
                str(item.get("end_time") or ""),
                str(item.get("title") or ""),
            )
        )
        return normalize_event_report(
            {"overview": report.get("overview"), "events": selected},
            max_events=max_events,
            max_minor_events=max(len(minor), 1) if minor else 0,
            max_anchors=max_anchors,
            include_links=include_links,
        )

    async def _refine_major_batch(
        self,
        batch: List[Dict[str, Any]],
        messages: List[dict],
        *,
        max_anchors: int,
        include_links: bool,
        detail_level: str,
        allow_single_retry: bool = True,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """批量精炼主要事件；失败事件原样返回，不影响话题索引完整性。

        多事件批量输出对推理型模型（输出预算与思考 token 共享）容易触发
        MAX_TOKENS 截断：JSON 写到中途被切断，整批解析失败或后半批候选缺失。
        因此批量结果不完整时，未精炼的候选会以单事件方式各重试一次——单事件
        输出最短，是同一预算下最可能完整返回的形态。"""

        source_by_id: Dict[str, List[dict]] = {}
        candidate_blocks: List[str] = []
        for index, event in enumerate(batch, start=1):
            candidate_id = f"E{index}"
            source = self._messages_for_event(
                event,
                messages,
                padding_minutes=3,
                max_messages=70,
            )
            source_by_id[candidate_id] = source
            source_text = self._format_event_messages(source)
            if len(source_text) > 5000:
                source_text = source_text[:5000]
            candidate_blocks.append(
                f"### {candidate_id}\n"
                f"候选：{event.get('start_time')}—{event.get('end_time')}｜"
                f"{event.get('title')}\n"
                f"event_id:{event.get('event_id')}\n"
                f"初步概括：{event.get('summary')}\n"
                f"对应原消息：\n{source_text}"
            )
        prompt = f"""你是一名严谨的群聊事件编辑。下面是 {len(batch)} 个
主要事件候选及其对应原消息。请逐个补充详细经过、明确结论、待确认事项、必要参与者、
重要链接和回查原话。{self._event_detail_instruction(detail_level)}

要求：
1. 每个输入 candidate_id 最多输出一个事件，不要合并不同 ID。
2. 时间、参与者、链接和原话只能来自对应 ID 的原消息。
3. summary 详细说明发生过程和背景；facts、outcomes、pending 的每个声明都必须绑定
   对应原消息中的真实 id，并尽量复用原消息的连续措辞以通过严格文本核验。
   outcomes 只写已经确认的结论；pending 写明确提出但未解决的事项。
4. 每个事件最多 {max_anchors} 条回查原话。
5. 无法进一步确认时仍返回候选事实，不得编造。
6. importance 始终为 major。status 只能是 suggested、inferred、discussed、decided、completed；
   不确定时使用 discussed。

{chr(10).join(candidate_blocks)}

只返回 JSON：
{{"events":[{{"candidate_id":"E1","importance":"major","status":"discussed",
"start_time":"09:20","end_time":"10:05","title":"标题","summary":"详细经过",
"facts":[{{"claim":"事实","evidence_ids":["消息id"]}}],
"outcomes":[{{"claim":"已确认结果","evidence_ids":["消息id"]}}],
"pending":[{{"claim":"待处理事项","evidence_ids":["消息id"]}}],
"participants":[],"anchors":[],"links":[]}}]}}"""
        prompt += (
            "\n\nHard constraints for refinement: only compress, reorder, deduplicate, or "
            "improve the wording of the supplied candidate and source messages. Do not add "
            "people, times, causes, conclusions, outcomes, or pending items. Preserve the "
            "candidate event_id and return it unchanged. Every fact, outcome, and pending "
            "claim must retain a source evidence_ids binding; omit anything that cannot be "
            "grounded."
        )
        result = await self._llm(
            prompt,
            request_type="plugin.daily_event.refine",
            temperature=0.2,
            model_task=self.refine_model,
        )
        parsed = self._parse_llm_json_object(result or "")
        raw_events = parsed.get("events") if isinstance(parsed, dict) else []
        if not isinstance(raw_events, list):
            raw_events = []
        raw_by_id = {
            str(item.get("candidate_id") or ""): item
            for item in raw_events
            if isinstance(item, dict)
        }

        refined: List[Dict[str, Any]] = []
        refined_flags: List[bool] = []
        success_count = 0
        for index, candidate in enumerate(batch, start=1):
            candidate_id = f"E{index}"
            raw = raw_by_id.get(candidate_id)
            candidate_title = str(candidate.get("title") or "")
            if not raw:
                self.logger.warning(
                    f"精炼响应缺少候选 {candidate_id}（{candidate_title}）"
                )
                refined.append(candidate)
                refined_flags.append(False)
                continue
            grounded = self._ground_event_report(
                {"overview": "", "events": [{**raw, "importance": "major"}]},
                source_by_id.get(candidate_id) or [],
                max_events=1,
                max_minor_events=0,
                max_anchors=max_anchors,
                include_links=include_links,
            )
            if not grounded.get("events"):
                self.logger.warning(
                    f"候选 {candidate_id}（{candidate_title}）的精炼结果"
                    "未通过事实锚定校验"
                )
                refined.append(candidate)
                refined_flags.append(False)
                continue
            event = grounded["events"][0]
            # The candidate ID is authoritative; a refinement model must not
            # be able to fork the event identity or create a new one.
            event["event_id"] = str(
                candidate.get("event_id") or event.get("event_id") or ""
            )
            event["source_message_count"] = candidate.get(
                "source_message_count", 0
            )
            event["source_participant_count"] = candidate.get(
                "source_participant_count", 0
            )
            event["refined"] = True
            refined.append(event)
            refined_flags.append(True)
            success_count += 1

        if allow_single_retry and len(batch) > 1 and success_count < len(batch):
            for index, succeeded in enumerate(refined_flags):
                if succeeded:
                    continue
                single_events, single_success = await self._refine_major_batch(
                    [batch[index]],
                    messages,
                    max_anchors=max_anchors,
                    include_links=include_links,
                    detail_level=detail_level,
                    allow_single_retry=False,
                )
                if single_success:
                    refined[index] = single_events[0]
                    success_count += 1
                else:
                    self.logger.warning(
                        f"候选 E{index + 1}"
                        f"（{str(batch[index].get('title') or '')}）"
                        "单事件重试仍未精炼成功，保留简略候选"
                    )
        return refined, success_count

    async def _refine_major_events(
        self,
        report: Dict[str, Any],
        messages: List[dict],
        *,
        max_anchors: int,
        include_links: bool,
        detail_level: str,
        refine_batch_size: int = _MAJOR_REFINE_BATCH_SIZE,
    ) -> Dict[str, Any]:
        major, minor = partition_events(report.get("events") or [])
        if not major:
            report["refinement"] = {
                "requested": 0,
                "refined": 0,
                "fallback": 0,
            }
            return report
        batch_size = max(
            1, min(8, int(refine_batch_size or _MAJOR_REFINE_BATCH_SIZE))
        )
        batches = [
            major[index : index + batch_size]
            for index in range(0, len(major), batch_size)
        ]
        outcomes = await asyncio.gather(
            *(
                self._refine_major_batch(
                    batch,
                    messages,
                    max_anchors=max_anchors,
                    include_links=include_links,
                    detail_level=detail_level,
                )
                for batch in batches
            )
        )
        refined_major = [event for events, _ in outcomes for event in events]
        refined_count = sum(count for _, count in outcomes)
        combined = refined_major + minor
        combined.sort(
            key=lambda item: (
                str(item.get("start_time") or ""),
                str(item.get("end_time") or ""),
                str(item.get("title") or ""),
            )
        )
        report["events"] = combined
        report["refinement"] = {
            "requested": len(major),
            "refined": refined_count,
            "fallback": len(major) - refined_count,
        }
        return report

    async def _verify_major_batch(
        self,
        batch: List[Dict[str, Any]],
        messages: List[dict],
    ) -> Tuple[List[Dict[str, Any]], int]:
        """独立复核声明；模型只能要求删除声明或下调事件状态。"""

        blocks: List[str] = []
        for event in batch:
            source = self._messages_for_event(
                event, messages, padding_minutes=3, max_messages=70
            )
            source_text = self._format_event_messages(source)[:5000]
            claims = {
                "facts": event.get("fact_bindings") or [],
                "outcomes": event.get("outcome_bindings") or [],
                "pending": event.get("pending_bindings") or [],
            }
            blocks.append(
                f"### {event.get('event_id')}\n"
                f"当前状态：{event.get('status', 'discussed')}\n"
                f"待复核声明：{json.dumps(claims, ensure_ascii=False)}\n"
                f"原消息：\n{source_text}"
            )

        prompt = f"""你是独立事实复核员。请逐个核对下面群聊事件中的声明与其 evidence_ids。

硬性限制：
1. 你不能改写或新增任何声明，也不能新增人物、时间、因果、结果或待办。
2. unsupported 中只能逐字复制输入声明里不受对应原消息支持的 claim。
3. status 只能保持不变或降级，等级从低到高为 suggested、inferred、discussed、decided、completed。
4. 即使全部支持，也必须返回该 event_id 和空的 unsupported。

{chr(10).join(blocks)}

只返回 JSON：
{{"events":[{{"event_id":"event-id","status":"discussed","unsupported":{{
"facts":[],"outcomes":[],"pending":[]}}}}]}}"""
        result = await self._llm(
            prompt,
            request_type="plugin.daily_event.verify",
            temperature=0.0,
            model_task=self.verify_model,
        )
        parsed = self._parse_llm_json_object(result or "")
        raw_events = parsed.get("events") if isinstance(parsed, dict) else None
        if not isinstance(raw_events, list):
            return list(batch), 0
        directives = {
            str(item.get("event_id") or ""): item
            for item in raw_events
            if isinstance(item, dict)
        }
        status_rank = {
            "suggested": 0,
            "inferred": 1,
            "discussed": 2,
            "decided": 3,
            "completed": 4,
        }
        verified: List[Dict[str, Any]] = []
        verified_count = 0
        for original in batch:
            event = dict(original)
            directive = directives.get(str(event.get("event_id") or ""))
            if not directive:
                verified.append(event)
                continue
            unsupported = directive.get("unsupported")
            if not isinstance(unsupported, dict):
                unsupported = {}
            for plural, bindings_key in (
                ("facts", "fact_bindings"),
                ("outcomes", "outcome_bindings"),
                ("pending", "pending_bindings"),
            ):
                existing = {
                    str(binding.get("claim") or "")
                    for binding in event.get(bindings_key) or []
                    if isinstance(binding, dict)
                }
                removals = {
                    str(claim).strip()
                    for claim in unsupported.get(plural) or []
                    if str(claim).strip() in existing
                }
                event[plural] = [
                    claim for claim in event.get(plural) or [] if claim not in removals
                ]
                event[bindings_key] = [
                    binding
                    for binding in event.get(bindings_key) or []
                    if binding.get("claim") not in removals
                ]
            current_status = str(event.get("status") or "discussed")
            requested_status = str(directive.get("status") or current_status).lower()
            if (
                requested_status in status_rank
                and status_rank[requested_status] <= status_rank.get(current_status, 2)
            ):
                event["status"] = requested_status
            if event.get("facts"):
                verified.append(event)
            verified_count += 1
        return verified, verified_count

    async def _verify_major_events(
        self,
        report: Dict[str, Any],
        messages: List[dict],
    ) -> Dict[str, Any]:
        major, minor = partition_events(report.get("events") or [])
        if not major:
            report["verification"] = {"requested": 0, "verified": 0, "fallback": 0}
            return report
        if not self.verify_model:
            report["verification"] = {
                "requested": len(major),
                "verified": 0,
                "fallback": len(major),
            }
            return report
        batches = [
            major[index : index + _MAJOR_REFINE_BATCH_SIZE]
            for index in range(0, len(major), _MAJOR_REFINE_BATCH_SIZE)
        ]
        outcomes = await asyncio.gather(
            *(self._verify_major_batch(batch, messages) for batch in batches)
        )
        verified_major = [event for events, _ in outcomes for event in events]
        verified_count = sum(count for _, count in outcomes)
        combined = verified_major + minor
        combined.sort(
            key=lambda item: (
                str(item.get("start_time") or ""),
                str(item.get("end_time") or ""),
                str(item.get("title") or ""),
            )
        )
        report["events"] = combined
        report["verification"] = {
            "requested": len(major),
            "verified": verified_count,
            "fallback": len(major) - verified_count,
        }
        return report

    def _event_range(self, messages: List[dict]) -> Dict[str, Any]:
        timestamps = [
            float(message.get("time") or 0)
            for message in messages
            if float(message.get("time") or 0) > 0
        ]
        if not timestamps:
            return {
                "start_time": "?",
                "end_time": "?",
                "message_count": len(messages),
            }
        return {
            "start_time": self._event_datetime(min(timestamps)).strftime("%H:%M"),
            "end_time": self._event_datetime(max(timestamps)).strftime("%H:%M"),
            "message_count": len(messages),
        }

    async def _extract_event_chunk_resilient(
        self,
        messages: List[dict],
        *,
        max_anchors: int,
        include_links: bool,
        detail_level: str,
        chunk_label: str,
        chunk_count: int,
        retry_count: int,
        split_on_timeout: bool,
        split_depth: int = 0,
    ) -> List[Dict[str, Any]]:
        """失败时重试；仍失败则把当前时段一分为二，绝不静默吞掉缺失区间。"""

        retries = max(0, min(3, int(retry_count or 0)))
        for attempt in range(retries + 1):
            report = await self._extract_event_chunk(
                messages,
                max_anchors=max_anchors,
                include_links=include_links,
                detail_level=detail_level,
                chunk_label=chunk_label,
                chunk_count=chunk_count,
            )
            if report is not None:
                return [
                    {
                        "success": True,
                        "report": report,
                        "message_count": len(messages),
                        "retried": attempt > 0,
                        "from_split": split_depth > 0,
                        **self._event_range(messages),
                    }
                ]

        can_split = (
            split_on_timeout
            and split_depth < 1
            and len(messages) >= 40
        )
        if can_split:
            midpoint = len(messages) // 2
            left, right = messages[:midpoint], messages[midpoint:]
            nested = await asyncio.gather(
                self._extract_event_chunk_resilient(
                    left,
                    max_anchors=max_anchors,
                    include_links=include_links,
                    detail_level=detail_level,
                    chunk_label=f"{chunk_label}.1",
                    chunk_count=chunk_count,
                    retry_count=retry_count,
                    split_on_timeout=split_on_timeout,
                    split_depth=split_depth + 1,
                ),
                self._extract_event_chunk_resilient(
                    right,
                    max_anchors=max_anchors,
                    include_links=include_links,
                    detail_level=detail_level,
                    chunk_label=f"{chunk_label}.2",
                    chunk_count=chunk_count,
                    retry_count=retry_count,
                    split_on_timeout=split_on_timeout,
                    split_depth=split_depth + 1,
                ),
            )
            return [item for group in nested for item in group]

        return [
            {
                "success": False,
                "report": {"overview": "", "events": []},
                "retried": retries > 0,
                "from_split": split_depth > 0,
                **self._event_range(messages),
            }
        ]

    async def analyze_group_event_report(
        self,
        messages: List[dict],
        *,
        max_events: int = 8,
        max_minor_events: int = 0,
        max_anchors: int = 2,
        max_input_messages: int = 1200,
        include_links: bool = True,
        coverage_mode: str = "balanced",
        detail_level: str = "standard",
        chunk_messages: int = _EVENT_CHUNK_MESSAGES,
        chunk_characters: int = _EVENT_CHUNK_CHARACTERS,
        retry_count: int = 2,
        split_on_timeout: bool = True,
        refine_major_events: bool = True,
        refine_batch_size: int = _MAJOR_REFINE_BATCH_SIZE,
    ) -> Dict[str, Any]:
        """全量建立轻量话题索引，再按需精炼主要事件。"""

        usable = [
            message
            for message in messages
            if str(message.get("processed_plain_text") or "").strip()
            and not message.get("is_command")
            and not message.get("is_notify")
        ]
        full_coverage = coverage_mode == "full"
        selected = (
            list(usable)
            if full_coverage
            else self._select_time_balanced_messages(usable, max_input_messages)
        )
        sampled = len(selected) < len(usable)
        chunks = self._chunk_event_messages(
            selected,
            max_messages=chunk_messages,
            max_characters=chunk_characters,
        )
        if not chunks:
            return {
                "overview": "",
                "events": [],
                "sampled": sampled,
                "analyzed_message_count": 0,
                "partial": False,
                "coverage": {
                    "mode": coverage_mode,
                    "total_messages": len(usable),
                    "selected_messages": len(selected),
                    "analyzed_messages": 0,
                    "coverage_percent": 0.0,
                    "chunks_total": 0,
                    "chunks_success": 0,
                    "chunks_failed": 0,
                    "retry_success": 0,
                    "split_chunks": 0,
                    "failed_ranges": [],
                },
            }

        chunk_tasks = [
            asyncio.create_task(
                self._extract_event_chunk_resilient(
                    chunk,
                    max_anchors=max_anchors,
                    include_links=include_links,
                    detail_level=detail_level,
                    chunk_label=str(index),
                    chunk_count=len(chunks),
                    retry_count=retry_count,
                    split_on_timeout=split_on_timeout,
                )
            )
            for index, chunk in enumerate(chunks, start=1)
        ]
        cancelled = False
        try:
            nested_outcomes = await asyncio.gather(*chunk_tasks)
        except asyncio.CancelledError:
            # A group-level timeout must not erase chunks that already finished.
            # Stop pending calls, keep their deterministic time ranges, and let
            # the caller deliver a clearly marked partial report.
            cancelled = True
            for task in chunk_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*chunk_tasks, return_exceptions=True)
            nested_outcomes = []
            for index, (task, chunk) in enumerate(zip(chunk_tasks, chunks), start=1):
                if task.done() and not task.cancelled() and task.exception() is None:
                    nested_outcomes.append(task.result())
                else:
                    nested_outcomes.append(
                        [
                            {
                                "success": False,
                                "report": {"overview": "", "events": []},
                                "retried": False,
                                "from_split": False,
                                **self._event_range(chunk),
                            }
                        ]
                    )
        outcomes = [item for group in nested_outcomes for item in group]
        successful = [item for item in outcomes if item.get("success")]
        failed = [item for item in outcomes if not item.get("success")]
        reports = [item["report"] for item in successful]

        if not reports:
            merged = {"overview": "", "events": []}
        else:
            # 两种覆盖模式都使用本地确定性合并。第一阶段不再增加一次全局模型
            # 合并调用，避免额外模型请求成为新的超时点。
            merged = merge_event_reports_fallback(
                reports,
                max_events=_EVENT_CANDIDATE_HARD_LIMIT,
                max_minor_events=_EVENT_CANDIDATE_HARD_LIMIT,
                max_anchors=max_anchors,
                include_links=include_links,
            )
        merged = self._prepare_event_candidates(
            merged,
            selected,
            max_events=max_events,
            max_minor_events=max_minor_events,
            max_anchors=max_anchors,
            include_links=include_links,
        )
        if not cancelled and refine_major_events and merged.get("events"):
            merged = await self._refine_major_events(
                merged,
                selected,
                max_anchors=max_anchors,
                include_links=include_links,
                detail_level=detail_level,
                refine_batch_size=refine_batch_size,
            )
        else:
            major, _ = partition_events(merged.get("events") or [])
            merged["refinement"] = {
                "requested": len(major),
                "refined": 0,
                "fallback": len(major),
            }
        if not cancelled:
            merged = await self._verify_major_events(merged, selected)
        else:
            major, _ = partition_events(merged.get("events") or [])
            merged["verification"] = {
                "requested": len(major),
                "verified": 0,
                "fallback": len(major),
            }
        analyzed_messages = sum(
            int(item.get("message_count") or 0) for item in successful
        )
        coverage_percent = (
            min(100.0, analyzed_messages * 100.0 / len(usable))
            if usable
            else 100.0
        )
        failed_ranges = [
            {
                "start_time": item.get("start_time") or "?",
                "end_time": item.get("end_time") or "?",
                "message_count": int(item.get("message_count") or 0),
            }
            for item in failed
        ]
        merged["sampled"] = sampled
        merged["partial"] = bool(failed) or cancelled
        merged["analyzed_message_count"] = analyzed_messages
        merged["coverage"] = {
            "mode": coverage_mode,
            "total_messages": len(usable),
            "selected_messages": len(selected),
            "analyzed_messages": analyzed_messages,
            "coverage_percent": round(coverage_percent, 1),
            "chunks_total": len(outcomes),
            "chunks_success": len(successful),
            "chunks_failed": len(failed),
            "retry_success": sum(
                1 for item in successful if item.get("retried")
            ),
            "split_chunks": sum(
                1 for item in outcomes if item.get("from_split")
            ),
            "failed_ranges": failed_ranges,
        }
        return merged

    # ==================== 单用户分析（LLM） ====================

    async def analyze_single_user_portrait(
        self, user_messages: List[dict], user_name: str, user_id: str
    ) -> Optional[Dict]:
        """生成只描述可观察事实的单用户画像。"""
        try:
            if not user_messages:
                return None

            samples = []
            for msg in user_messages:
                text = re.sub(
                    r"\s+", " ", str(msg.get("processed_plain_text") or "")
                ).strip()
                if len(text) >= 5 and len(samples) < 20:
                    samples.append(text[:160])

            if not samples:
                return None

            samples_text = "\n".join(f"- {s}" for s in samples)

            stats = self.analyze_single_user_stats(user_messages)
            avg_chars = stats["char_count"] / stats["message_count"] if stats["message_count"] else 0
            emoji_ratio = stats["emoji_count"] / stats["message_count"] if stats["message_count"] else 0
            hours = stats["hours"]
            peak_hour = max(range(24), key=lambda hour: hours[hour])
            activity_pattern = (
                f"样本中 {peak_hour:02d}:00—{(peak_hour + 1) % 24:02d}:00 发言最多，"
                f"共 {hours[peak_hour]} 条"
            )

            prompt = f"""根据下面明确给出的个人发言样本和本地统计，生成事实型群聊画像。

用户：{user_name}
发言数：{stats['message_count']}条
平均字数：{avg_chars:.1f}字/条
表情比例：{emoji_ratio:.2f}
活跃时段事实：{activity_pattern}

发言样本：
{samples_text}

硬性要求：
1. 只描述样本中能直接观察到的话题和表达方式，不推断 MBTI、心理状态、隐藏动机、
   职业、地域、年龄、性别、健康状况或其他敏感属性。
2. 明确区分“发言中提到”“提出建议”“表示准备做”和“实际完成”，不得把前者写成完成事实。
3. evidence_points 每项都要逐字复制一段真实短原话，并给出仅由该原话支持的观察。
4. summary 为 60—120 字事实型概括；communication_style 只描述可观察的句式、长度、
   提问/陈述倾向，不作性格评价。

返回JSON（不要markdown代码块，不要emoji）：
{{
  "name": "{user_name}",
  "summary": "事实型概括",
  "topics": ["样本中实际讨论的话题"],
  "communication_style": "可观察表达特点",
  "evidence_points": [{{"quote": "真实短原话", "observation": "由原话直接支持的观察"}}]
}}"""

            result = await self._llm(
                prompt,
                request_type="plugin.chat_summary.single_user_portrait",
                temperature=0.2,
                model_task=self.user_profile_model,
            )
            if result is None:
                return None

            data = self._parse_llm_json_object(result)
            if not data:
                return None

            raw_topics = data.get("topics")
            topics = []
            if isinstance(raw_topics, list):
                for item in raw_topics:
                    topic = re.sub(r"\s+", " ", str(item or "")).strip()[:40]
                    if topic and topic not in topics:
                        topics.append(topic)
                    if len(topics) >= 6:
                        break
            evidence_points = []
            for item in data.get("evidence_points") or []:
                if not isinstance(item, dict):
                    continue
                quote = re.sub(r"\s+", " ", str(item.get("quote") or "")).strip()[:100]
                observation = re.sub(
                    r"\s+", " ", str(item.get("observation") or "")
                ).strip()[:140]
                if quote and observation and any(quote in sample for sample in samples):
                    evidence_points.append(
                        {"quote": quote, "observation": observation}
                    )
                if len(evidence_points) >= 4:
                    break

            return {
                "name": str(data.get("name", user_name))[:50],
                "summary": re.sub(
                    r"\s+", " ", str(data.get("summary") or "")
                ).strip()[:240],
                "topics": topics,
                "activity_pattern": activity_pattern,
                "communication_style": re.sub(
                    r"\s+", " ", str(data.get("communication_style") or "")
                ).strip()[:180],
                "evidence_points": evidence_points,
                "user_id": user_id,
            }

        except Exception as e:
            self.logger.error(f"生成单用户画像失败: {e}", exc_info=True)
            return None

    # ==================== LLM JSON 解析（静态） ====================

    @classmethod
    def _parse_llm_json_object(cls, result: str) -> Optional[Dict[str, Any]]:
        """解析 LLM 返回的 JSON 对象（非数组）"""
        try:
            result = result.strip()
            if result.startswith("```"):
                parts = result.split("```")
                if len(parts) >= 2:
                    result = parts[1]
                    if result.startswith("json"):
                        result = result[4:]
            result = result.strip()

            start_idx = result.find("{")
            end_idx = result.rfind("}")
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                result = result[start_idx : end_idx + 1]

            data = json.loads(result)
            if not isinstance(data, dict):
                return None
            return data

        except json.JSONDecodeError:
            try:
                result_cleaned = cls.EMOJI_PATTERN.sub("", result)
                start_idx = result_cleaned.find("{")
                end_idx = result_cleaned.rfind("}")
                if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                    result_cleaned = result_cleaned[start_idx : end_idx + 1]
                result_cleaned = re.sub(
                    r"([一-鿿])\s+([一-鿿])", r"\1\2", result_cleaned
                )
                data = json.loads(result_cleaned)
                return data if isinstance(data, dict) else None
            except Exception:
                return None
        except Exception:
            return None

    @classmethod
    def _parse_llm_json(cls, result: str) -> List[Dict[str, Any]]:
        """解析 LLM 返回的 JSON 数组（带 emoji 清理与截断修复 fallback）"""
        result_cleaned = ""
        try:
            result = result.strip()
            if result.startswith("```"):
                parts = result.split("```")
                if len(parts) >= 2:
                    result = parts[1]
                    if result.startswith("json"):
                        result = result[4:]
            result = result.strip()

            start_idx = result.find("[")
            end_idx = result.rfind("]")
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                result = result[start_idx : end_idx + 1]

            data = json.loads(result)
            if not isinstance(data, list):
                return []
            if data and not all(isinstance(item, dict) for item in data):
                return []
            return data

        except json.JSONDecodeError:
            try:
                result_cleaned = cls.EMOJI_PATTERN.sub("", result)
                start_idx = result_cleaned.find("[")
                end_idx = result_cleaned.rfind("]")
                if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                    result_cleaned = result_cleaned[start_idx : end_idx + 1]
                elif start_idx != -1:
                    result_cleaned = result_cleaned[start_idx:]

                result_cleaned = re.sub(
                    r"([一-鿿])\s+([一-鿿])", r"\1\2", result_cleaned
                )
                result_cleaned = re.sub(r"([一-鿿])\s+([\d])", r"\1\2", result_cleaned)
                result_cleaned = re.sub(r"([\d])\s+([一-鿿])", r"\1\2", result_cleaned)

                try:
                    data = json.loads(result_cleaned)
                except json.JSONDecodeError:
                    result_fixed = cls._fix_truncated_json_array(result_cleaned)
                    if result_fixed:
                        data = json.loads(result_fixed)
                    else:
                        raise

                if not isinstance(data, list):
                    return []
                if data and not all(isinstance(item, dict) for item in data):
                    return []
                return data
            except Exception:
                return []
        except Exception:
            return []

    @staticmethod
    def _fix_truncated_json_array(json_str: str) -> Optional[str]:
        """尝试修复被截断的 JSON 数组"""
        try:
            brace_positions = []
            in_string = False
            escape_next = False

            for i, char in enumerate(json_str):
                if escape_next:
                    escape_next = False
                    continue
                if char == "\\":
                    escape_next = True
                    continue
                if char == '"' and not escape_next:
                    in_string = not in_string
                    continue
                if char == "}" and not in_string:
                    brace_positions.append(i)

            for pos in reversed(brace_positions):
                candidate = json_str[: pos + 1].rstrip()
                if candidate.endswith(","):
                    candidate = candidate[:-1]
                candidate = candidate + "\n]"
                try:
                    data = json.loads(candidate)
                    if isinstance(data, list) and len(data) > 0:
                        return candidate
                except json.JSONDecodeError:
                    continue

            return None
        except Exception:
            return None
