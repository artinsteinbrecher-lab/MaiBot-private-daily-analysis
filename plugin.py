"""MaiBot 私聊群事件日报（MaiBot 1.0 / maibot_sdk 2.x）。

``/summary`` 仅接受管理员 QQ 私聊，用于总结一个来源群或全部白名单群；
定时任务在次日生成前一个完整自然日的报告。进度和结果只发送到目标私聊，
事件日报不会向来源群发送内容。还可通过“绝对静默”名单阻止指定来源群的
所有出站消息；原版 ``/mysummary`` 个人总结功能继续保留。

所有宿主能力通过官方 ``ctx.*`` API 调用，图片由 ``render.html2png`` 渲染。
"""

import asyncio
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Literal, Optional, Tuple

from maibot_sdk import Command, Field, HookHandler, MaiBotPlugin, PluginConfigBase
from maibot_sdk.types import ErrorPolicy, HookMode, HookOrder

from .core import AnalysisService, SummaryRenderer
from .core.event_digest import (
    build_daily_index_text,
    build_event_plain_text,
    parse_summary_command,
    split_message_text,
)


# ==================== 模块选项（WebUI 中文下拉）====================

# 个人总结可选模块；额外提供"并排"组合项以保留横向并排能力
PersonalModuleOption = Literal[
    "无", "3H活跃轨迹", "群友画像", "炫压抑评级", "语出惊人", "群友画像+炫压抑评级(并排)"
]

_PERSONAL_MODULE_MAP = {
    "3H活跃轨迹": "3H",
    "群友画像": "Portraits",
    "炫压抑评级": "Rankings",
    "语出惊人": "Quotes",
    "群友画像+炫压抑评级(并排)": "Portraits,Rankings",
}

def _slots_to_display_order(slots, mapping: dict) -> List[str]:
    """把若干下拉槽位（中文模块名，"无"表示不显示）按顺序转成渲染器用的代码列表，按模块去重。

    组合项（如 "Portraits,Rankings"）按其成员逐个去重：若某成员已在前面出现过，则跳过该槽位，
    避免同一模块在"独立"和"并排"中重复渲染。
    """
    order: List[str] = []
    seen = set()
    for slot in slots:
        code = mapping.get(slot)
        if not code:
            continue
        members = code.split(",")
        if any(member in seen for member in members):
            continue
        seen.update(members)
        order.append(code)
    return order


# ==================== 配置模型 ====================


class PluginSection(PluginConfigBase):
    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0
    enabled: bool = Field(
        default=False,
        description="是否启用插件",
        json_schema_extra={"label": "启用插件"},
    )
    config_version: str = Field(
        default="3.1.0",
        description="配置文件版本，用于兼容性校验，请勿手动修改",
        json_schema_extra={"label": "配置版本", "disabled": True},
    )


class SummarySection(PluginConfigBase):
    __ui_label__ = "事件日报"
    __ui_icon__ = "file-text"
    __ui_order__ = 1
    max_events: int = Field(
        default=8,
        description="每个群最多展示的主要事件数量；普通寒暄、复读和表情刷屏会被忽略",
        json_schema_extra={"label": "每群最多事件数", "hint": "推荐 8"},
    )
    anchors_per_event: int = Field(
        default=2,
        description="每个事件保留的带时间戳原话数量，方便回到 QQ 聊天记录定位",
        json_schema_extra={"label": "每事件回查锚点", "hint": "推荐 2"},
    )
    events_per_page: int = Field(
        default=4,
        description="每张详情图片展示的事件数量，超出时自动分页",
        json_schema_extra={"label": "每张图片事件数", "hint": "推荐 4"},
    )
    max_input_messages: int = Field(
        default=1200,
        description="单群单次最多用于分析的消息数；高流量群超过后按全天时段均衡抽样",
        json_schema_extra={"label": "每群最大分析消息数", "hint": "推荐 1200"},
    )
    include_anchor_quotes: bool = Field(
        default=True,
        description="是否在报告中展示带精确时间和发言人的原话片段",
        json_schema_extra={"label": "显示回查原话"},
    )
    include_links: bool = Field(
        default=True,
        description="保留事件中出现的重要网址，便于后续回查",
        json_schema_extra={"label": "保留重要链接"},
    )


class UserSummarySection(PluginConfigBase):
    __ui_label__ = "个人总结"
    __ui_icon__ = "user"
    __ui_order__ = 2
    enabled: bool = Field(
        default=True,
        description="是否启用个人总结功能（关闭后所有人都无法使用 /mysummary）",
        json_schema_extra={"label": "启用个人总结"},
    )
    view_others_mode: Literal["白名单", "黑名单"] = Field(
        default="白名单",
        description="查看他人总结的名单模式：白名单=仅名单内用户可看他人；黑名单=名单内用户禁止看他人，其余人可看",
        json_schema_extra={"label": "查看他人名单模式"},
    )
    allowed_users: List[str] = Field(
        default_factory=list,
        description="配合上面的名单模式控制谁能查看他人总结。所有人始终可以查看自己。",
        json_schema_extra={
            "label": "查看他人名单",
            "hint": "为空时所有人都能查看他人；白名单=仅名单内可看他人；黑名单=名单内禁止看他人",
        },
    )
    # 4 个下拉槽位，按槽位顺序显示；选"无"隐藏；含"并排"组合项
    slot_1: PersonalModuleOption = Field(
        default="3H活跃轨迹",
        description="第 1 个显示的模块（选『无』则此位置不显示）",
        json_schema_extra={"label": "显示模块 1"},
    )
    slot_2: PersonalModuleOption = Field(
        default="群友画像+炫压抑评级(并排)",
        description="第 2 个显示的模块（『…并排』表示两个模块横向并排）",
        json_schema_extra={"label": "显示模块 2"},
    )
    slot_3: PersonalModuleOption = Field(
        default="语出惊人",
        description="第 3 个显示的模块",
        json_schema_extra={"label": "显示模块 3"},
    )
    slot_4: PersonalModuleOption = Field(
        default="无",
        description="第 4 个显示的模块",
        json_schema_extra={"label": "显示模块 4"},
    )


class AutoSummarySection(PluginConfigBase):
    __ui_label__ = "自动总结"
    __ui_icon__ = "clock"
    __ui_order__ = 3
    enabled: bool = Field(
        default=False,
        description="是否启用每日自动总结",
        json_schema_extra={"label": "启用每日自动总结"},
    )
    time: str = Field(
        default="00:10",
        description="次日生成前一天完整日报的时间（HH:MM，24小时制）",
        json_schema_extra={"label": "执行时间"},
    )
    timezone: str = Field(
        default="Asia/Shanghai",
        description="时区设置（IANA 名称，如 Asia/Shanghai）",
        json_schema_extra={"label": "时区"},
    )
    min_messages: int = Field(
        default=10,
        description="生成总结所需的最少消息数量",
        json_schema_extra={"label": "最少消息数"},
    )
    target_chats: List[str] = Field(
        default_factory=list,
        description="允许总结的 QQ 群号白名单；为空时自动和手动总结都不执行",
        json_schema_extra={"label": "允许总结的群聊", "hint": "必须明确填写群号"},
    )
    recipient_user: str = Field(
        default="",
        description="自动日报唯一接收 QQ；必须同时存在于管理员账号列表，留空时自动日报不生成",
        json_schema_extra={"label": "自动日报接收账号", "hint": "不填写就不执行自动日报"},
    )


class SilenceSection(PluginConfigBase):
    __ui_label__ = "绝对静默"
    __ui_icon__ = "volume-x"
    __ui_order__ = 4
    enabled: bool = Field(
        default=False,
        description="开启后，在真正发送到 QQ 前拦截静默名单群的所有出站消息；"
        "消息接收、入库和日报生成不受影响",
        json_schema_extra={"label": "启用绝对静默"},
    )
    target_chats: List[str] = Field(
        default_factory=list,
        description="需要绝对静默的 QQ 群号；只有同时存在于“允许总结的群聊”中的群号才生效",
        json_schema_extra={
            "label": "绝对静默群聊",
            "hint": "阻止普通回复、@回复、昵称触发、命令和其他插件向这些群发送消息",
        },
    )


class CommandPermissionSection(PluginConfigBase):
    __ui_label__ = "管理员账号"
    __ui_icon__ = "shield"
    __ui_order__ = 5
    admin_users: List[str] = Field(
        default_factory=list,
        description="允许私聊执行 /summary 的管理员 QQ；不同账号的请求和结果彼此独立",
        json_schema_extra={"label": "管理员 QQ 列表", "hint": "留空时无人可以执行手动总结"},
    )


class AdvancedSection(PluginConfigBase):
    __ui_label__ = "高级"
    __ui_icon__ = "settings"
    __ui_order__ = 6
    model_task: str = Field(
        default="utils",
        description="生成总结/分析使用的【模型任务名】。该任务内配置的模型会按其 model_list 随机/轮询使用。"
        "建议 utils 或 planner（通常是快速非思考模型）；replyer 是主回复模型，可能较慢、需配合调大 LLM 超时。",
        json_schema_extra={
            "label": "模型任务",
            "hint": "填 MaiBot 的【任务名】(如 utils / planner / replyer / memory)，不是模型名；"
            "想指定具体模型请在 MaiBot 的 model_config.toml 改该任务的 model_list。填错会自动回退 utils。",
        },
    )
    inject_memory: bool = Field(
        default=False,
        description="实验性：把生成的总结注入麦麦的会话上下文，供其记忆系统吸收。"
        "群聊总结注入到该群；个人总结注入为对该用户的记忆。",
        json_schema_extra={
            "label": "总结注入麦麦记忆（实验性）",
            "hint": "实验功能，默认关闭；开启后总结内容会进入麦麦记忆",
        },
    )
    llm_timeout_seconds: int = Field(
        default=60,
        description="单次 LLM 调用的最长等待时间（秒），到点放弃该次分析项。"
        "注意：宿主对插件的单次能力调用约有 30 秒 RPC 硬上限，设置大于 30 通常不会有额外效果。",
        json_schema_extra={"label": "LLM 调用超时（秒）", "hint": "默认 60；受宿主约 30 秒 RPC 上限约束"},
    )
    render_timeout_seconds: int = Field(
        default=25,
        description="单次图片渲染的超时时间（秒）。图片较复杂或机器较慢时可适当调大。",
        json_schema_extra={"label": "图片渲染超时（秒）", "hint": "默认 25"},
    )
    group_timeout_seconds: int = Field(
        default=300,
        description="单个群完成消息读取、事件提取和图片渲染的整体超时时间",
        json_schema_extra={"label": "单群整体超时（秒）", "hint": "Ubuntu/Docker 推荐 300"},
    )


class DailyAnalysisConfig(PluginConfigBase):
    plugin: PluginSection = Field(default_factory=PluginSection)
    summary: SummarySection = Field(default_factory=SummarySection)
    user_summary: UserSummarySection = Field(default_factory=UserSummarySection)
    auto_summary: AutoSummarySection = Field(default_factory=AutoSummarySection)
    silence: SilenceSection = Field(default_factory=SilenceSection)
    command_permission: CommandPermissionSection = Field(default_factory=CommandPermissionSection)
    advanced: AdvancedSection = Field(default_factory=AdvancedSection)


# ==================== 插件主类 ====================


class DailyAnalysisPlugin(MaiBotPlugin):
    """私聊群事件日报插件。"""

    config_model = DailyAnalysisConfig

    def __init__(self) -> None:
        super().__init__()
        self._service: Optional[AnalysisService] = None
        self._renderer: Optional[SummaryRenderer] = None
        self._scheduler_task: Optional[asyncio.Task] = None
        self._last_auto_date: Optional[date] = None
        # 正在生成总结的任务标识，防止同一会话/用户并发刷命令
        self._generating: set = set()
        # 后台总结任务集合（命令秒回、重活后台跑）；on_unload 时统一取消
        self._bg_tasks: set = set()

    # ---------- 生命周期 ----------

    async def on_load(self) -> None:
        adv = self.config.advanced
        model_task = await self._validated_model_task()
        self._service = AnalysisService(
            self.ctx,
            model_task,
            adv.llm_timeout_seconds,
            self.config.auto_summary.timezone,
        )
        self._renderer = SummaryRenderer(self.ctx, self._render_timeout_ms())
        self._start_scheduler()
        self.ctx.logger.info(
            f"私聊群事件日报插件已加载（模型任务: {model_task}，LLM超时: {adv.llm_timeout_seconds}s，"
            f"渲染超时: {adv.render_timeout_seconds}s）"
        )

    def _render_timeout_ms(self) -> int:
        return max(5, int(self.config.advanced.render_timeout_seconds or 25)) * 1000

    async def _validated_model_task(self) -> str:
        """校验配置的模型任务名是否为宿主可用任务；非法（如误填模型名）则回退 utils 并告警。

        ctx.llm.generate(model=...) 只接受【任务名】(resolve_task_name 对未知名抛 ValueError)，
        因此这里在加载/热更新时主动校验，避免误填导致每次分析静默失败。
        """
        want = (self.config.advanced.model_task or "utils").strip() or "utils"
        try:
            res = await self.ctx.llm.get_available_models()
            models = res.get("models") if isinstance(res, dict) else res
            if isinstance(models, list) and models:
                if want in models:
                    return want
                fallback = "utils" if "utils" in models else str(models[0])
                self.ctx.logger.warning(
                    f"配置的模型任务 '{want}' 不在可用任务列表 {models} 中"
                    f"（只能填任务名、不能填模型名），已回退到 '{fallback}'"
                )
                return fallback
        except Exception as e:
            self.ctx.logger.warning(f"校验模型任务可用性失败，按配置值 '{want}' 使用: {e}")
        return want

    async def on_unload(self) -> None:
        await self._stop_scheduler()
        await self._cancel_bg_tasks()
        self.ctx.logger.info("私聊群事件日报插件已卸载")

    def _spawn_bg(self, coro: Any) -> None:
        """创建并跟踪后台任务；完成后自动移除引用。命令秒回、重活放后台跑，
        绕开宿主对命令处理的 60 秒硬超时；任务集合在 on_unload 时统一取消。"""
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _cancel_bg_tasks(self) -> None:
        """卸载（on_unload）时取消所有未完成的后台总结任务并等待退出。

        注意：热重载（on_config_update）不调用本方法、也不取消在途任务——它只原地
        更新 _service/_renderer 的属性，在途任务持有同一对象引用、用的是不可变快照，
        属良性不一致；故意不在保存配置时掐断用户刚发起的 /summary。
        """
        tasks = list(self._bg_tasks)
        self._bg_tasks.clear()
        for t in tasks:
            if not t.done():
                t.cancel()
        for t in tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self.ctx.logger.warning(f"后台任务清理时异常: {e}")

    async def on_config_update(self, scope: str, config_data: dict, version: str) -> None:
        if scope != "self":
            return
        # 同步分析模型任务与超时设置
        if self._service is not None:
            self._service.model = await self._validated_model_task()
            self._service.call_timeout_s = max(5, int(self.config.advanced.llm_timeout_seconds or 60))
            self._service.timezone_name = (
                self.config.auto_summary.timezone or "Asia/Shanghai"
            )
        if self._renderer is not None:
            self._renderer.timeout_ms = self._render_timeout_ms()
        # 自动总结配置可能变化，重启调度器
        await self._stop_scheduler()
        self._start_scheduler()

    # ---------- WebUI 布局：每个配置节一个标签页（分页） ----------

    def get_webui_config_schema(self, **kwargs: Any) -> Dict[str, Any]:
        """在 SDK 自动生成的配置 Schema 基础上，把布局改为「每个配置节一个标签页」。

        这样 WebUI 会把『插件 / 事件日报 / 个人总结 / 自动总结 / 管理员账号 / 高级』
        分别渲染成可切换的页签，而不是堆在一页里。
        """
        try:
            schema = super().get_webui_config_schema(**kwargs)
        except Exception:
            # SDK 基类方法缺失/签名不兼容时，交还宿主走兜底，绝不让配置页崩
            return {}
        try:
            if isinstance(schema, dict):
                sections = schema.get("sections")
                if isinstance(sections, dict) and sections:
                    ordered = sorted(
                        sections.items(),
                        key=lambda kv: (kv[1].get("order", 0) if isinstance(kv[1], dict) else 0),
                    )
                    tabs = []
                    for name, sec in ordered:
                        sec = sec if isinstance(sec, dict) else {}
                        tabs.append(
                            {
                                "id": name,
                                "title": sec.get("title") or name,
                                "icon": sec.get("icon"),
                                "order": sec.get("order", 0),
                                "sections": [name],
                            }
                        )
                    schema["layout"] = {"type": "tabs", "tabs": tabs}
        except Exception:
            # 任何异常都不应影响配置 Schema 的返回，保持 SDK 原样
            return schema
        return schema

    # ---------- 工具：能力返回解析 ----------

    @staticmethod
    def _extract_list(result: Any, key: str) -> List[dict]:
        """从能力返回中稳健提取列表（兼容 list 或 {"success", key:[...]} envelope）"""
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        if isinstance(result, dict):
            if result.get("success") is False:
                return []
            val = result.get(key)
            if isinstance(val, list):
                return [item for item in val if isinstance(item, dict)]
        return []

    @staticmethod
    def _as_id_set(values: Any) -> set:
        """把配置中的 QQ/群号列表归一化为字符串集合"""
        out = set()
        for v in values or []:
            s = str(v).strip()
            if s:
                out.add(s)
        return out

    @staticmethod
    def _command_identity(
        stream_id: str,
        group_id: str,
        user_id: str,
        message: Any,
    ) -> Tuple[str, str, str]:
        """从 Command 官方传入的完整 message 对象补齐聊天流、群号和用户号。"""

        message = message if isinstance(message, dict) else {}
        info = (
            message.get("message_info")
            if isinstance(message.get("message_info"), dict)
            else {}
        )
        user_info = (
            info.get("user_info")
            if isinstance(info.get("user_info"), dict)
            else {}
        )
        group_info = (
            info.get("group_info")
            if isinstance(info.get("group_info"), dict)
            else {}
        )
        resolved_stream = str(
            stream_id
            or message.get("stream_id")
            or message.get("session_id")
            or info.get("stream_id")
            or ""
        )
        resolved_group = str(
            group_id
            or group_info.get("group_id")
            or message.get("group_id")
            or ""
        )
        resolved_user = str(
            user_id
            or user_info.get("user_id")
            or message.get("user_id")
            or ""
        )
        return resolved_stream, resolved_group, resolved_user

    # ---------- 消息查询与归一化 ----------

    @staticmethod
    def _text_from_segments(raw_message: list) -> str:
        """从 raw_message 段重建发言者本人的文本：仅取 text 段（at 段渲染为 @名字），
        刻意跳过 reply 段（被引用的原消息）。

        背景（真机验证）：当 B 回复 A 时，宿主会把『被引用的原消息文本』拼到 B 的
        processed_plain_text 前面（见宿主 src/chat/message_receive/message.py 的
        process_reply_component），导致 A 的话被误记成 B 所说。改从 raw_message 的
        text/at 段重建即可只拿到 B 本人的话。
        """
        parts: List[str] = []
        for seg in raw_message:
            if not isinstance(seg, dict):
                continue
            stype = seg.get("type")
            data = seg.get("data")
            if stype == "text" and isinstance(data, str):
                parts.append(data)
            elif stype == "at" and isinstance(data, dict):
                name = (
                    data.get("target_user_cardname")
                    or data.get("target_user_nickname")
                    or data.get("target_user_id")
                    or ""
                )
                if name:
                    parts.append(f"@{name}")
            # 跳过 reply / image / emoji / voice / forward 等段
        return " ".join(p for p in parts if p).strip()

    @classmethod
    def _normalize_message(cls, m: dict) -> Optional[dict]:
        """把新 SDK 的嵌套消息 dict 归一化为分析层需要的扁平结构"""
        if not isinstance(m, dict):
            return None
        info = m.get("message_info") or {}
        uinfo = info.get("user_info") or {}
        ginfo = info.get("group_info") or {}
        try:
            ts = float(m.get("timestamp") or 0)
        except (ValueError, TypeError):
            ts = 0.0
        # 丢弃非法/缺失时间戳的消息：datetime.fromtimestamp(<=0) 在 Windows 会抛 OSError
        if ts <= 0:
            return None

        # 回复消息文本修正：若该消息含 reply 段，宿主会把被引用的原消息文本拼进
        # processed_plain_text，故改用 raw_message 的 text/at 段重建发言者本人的话；
        # 非回复消息沿用 processed_plain_text（行为完全不变）。
        raw_segments = m.get("raw_message") or []
        has_reply = any(
            isinstance(s, dict) and s.get("type") == "reply" for s in raw_segments
        )
        text = (
            cls._text_from_segments(raw_segments)
            if has_reply
            else (m.get("processed_plain_text") or "")
        )

        return {
            "user_id": str(uinfo.get("user_id") or ""),
            "user_nickname": uinfo.get("user_nickname") or "未知用户",
            "user_cardname": uinfo.get("user_cardname") or "",
            "processed_plain_text": text,
            "time": ts,
            "is_command": bool(m.get("is_command")),
            "is_notify": bool(m.get("is_notify")),
            "group_id": str((ginfo or {}).get("group_id") or ""),
        }

    async def _get_messages(self, stream_id: str, start_ts: float, end_ts: float) -> List[dict]:
        """查询某个聊天流在 [start_ts, end_ts) 内的有效消息（已归一化、按时间正序）"""
        if not stream_id:
            return []
        try:
            result = await self.ctx.message.get_by_time_in_chat(
                stream_id,
                start_time=float(start_ts),
                end_time=float(end_ts),
                limit=0,
                limit_mode="earliest",
                filter_mai=False,
                filter_command=True,
            )
        except Exception as e:
            self.ctx.logger.error(f"查询消息失败 (stream={stream_id}): {e}", exc_info=True)
            return []

        raw_messages = self._extract_list(result, "messages")
        out: List[dict] = []
        for m in raw_messages:
            norm = self._normalize_message(m)
            if norm is None:
                continue
            # filter_command 已在宿主侧过滤命令；这里再排除通知类
            if norm["is_notify"]:
                continue
            out.append(norm)

        out.sort(key=lambda x: x["time"])
        return out

    # ---------- 时间范围 ----------

    @staticmethod
    def _parse_time_range(time_range: str) -> Tuple[Optional[float], Optional[float], Optional[datetime]]:
        """解析 今天/昨天 为 (start_ts, end_ts, target_date)，不支持返回 (None, None, None)"""
        now = datetime.now()
        today_start = datetime(now.year, now.month, now.day)
        if time_range in ("今天", ""):
            return today_start.timestamp(), now.timestamp(), now
        if time_range == "昨天":
            yesterday_start = today_start - timedelta(days=1)
            return yesterday_start.timestamp(), today_start.timestamp(), now - timedelta(days=1)
        return None, None, None

    # ---------- 权限 ----------

    def _check_group_permission(self, group_id: str) -> bool:
        """群号必须显式存在于统一的来源群白名单。"""

        target_chats = self._as_id_set(self.config.auto_summary.target_chats)
        return bool(target_chats) and str(group_id) in target_chats

    def _is_silent_group(self, group_id: str) -> bool:
        """只允许日报来源白名单中的群进入绝对静默名单。"""

        if not group_id or not self.config.silence.enabled:
            return False
        allowed_groups = self._as_id_set(self.config.auto_summary.target_chats)
        silent_groups = self._as_id_set(self.config.silence.target_chats)
        return str(group_id) in allowed_groups.intersection(silent_groups)

    @staticmethod
    def _outbound_group_id(message: Any) -> str:
        """从官方 send_service Hook 的序列化 SessionMessage 中读取群号。"""

        if not isinstance(message, dict):
            return ""
        message_info = message.get("message_info")
        if not isinstance(message_info, dict):
            return ""
        group_info = message_info.get("group_info")
        if not isinstance(group_info, dict):
            return ""
        return str(group_info.get("group_id") or "").strip()

    @HookHandler(
        "send_service.before_send",
        name="silent_group_send_guard",
        description="阻止绝对静默名单群的所有出站消息，同时保留消息接收和日报读取",
        mode=HookMode.BLOCKING,
        order=HookOrder.EARLY,
        timeout_ms=1000,
        error_policy=ErrorPolicy.ABORT,
    )
    async def guard_silent_group_send(self, **kwargs: Any) -> Dict[str, str]:
        """在 Platform IO 前做最终拦截，覆盖普通回复、命令和其他插件发送。"""

        group_id = self._outbound_group_id(kwargs.get("message"))
        if self._is_silent_group(group_id):
            self.ctx.logger.info(f"已阻止绝对静默群 {group_id} 的出站消息")
            return {"action": "abort"}
        return {"action": "continue"}

    # ==================== 后台总结任务（命令秒回，重活后台跑） ====================

    @staticmethod
    def _stream_field(stream: dict, *keys: str) -> Any:
        """兼容能力返回的直接字段和嵌套 stream/group_info 字段。"""

        containers = [
            stream,
            stream.get("stream") if isinstance(stream.get("stream"), dict) else {},
            stream.get("group_info") if isinstance(stream.get("group_info"), dict) else {},
        ]
        for container in containers:
            for key in keys:
                value = container.get(key)
                if value not in (None, ""):
                    return value
        return None

    def _group_meta(self, stream: dict) -> Optional[Dict[str, str]]:
        stream_id = str(
            self._stream_field(stream, "stream_id", "session_id") or ""
        )
        group_id = str(self._stream_field(stream, "group_id") or "")
        if not stream_id or not group_id:
            return None
        group_name = str(
            self._stream_field(
                stream,
                "group_name",
                "chat_name",
                "session_name",
                "name",
            )
            or f"群{group_id}"
        )
        return {
            "stream_id": stream_id,
            "group_id": group_id,
            "group_name": group_name,
            "account_id": str(self._stream_field(stream, "account_id", "self_id") or ""),
            "scope": str(self._stream_field(stream, "scope", "connection_id") or ""),
        }

    async def _get_configured_group_streams(
        self, target: str
    ) -> Tuple[List[Dict[str, str]], List[str]]:
        configured = [
            str(value).strip()
            for value in self.config.auto_summary.target_chats
            if str(value).strip()
        ]
        if not configured:
            return [], []

        result = await self.ctx.chat.get_group_streams(platform="qq")
        stream_map: Dict[str, Dict[str, str]] = {}
        for stream in self._extract_list(result, "streams"):
            meta = self._group_meta(stream)
            if meta:
                stream_map[meta["group_id"]] = meta

        wanted = configured if target == "全部" else [target]
        found = [stream_map[group_id] for group_id in wanted if group_id in stream_map]
        missing = [group_id for group_id in wanted if group_id not in stream_map]
        return found, missing

    async def _send_text_chunks(self, text: str, stream_id: str) -> bool:
        success = True
        for chunk in split_message_text(text):
            sent = await self.ctx.send.text(chunk, stream_id)
            success = bool(sent) and success
        return success

    @staticmethod
    def _extract_stream_id(result: Any) -> str:
        if isinstance(result, str):
            return result
        if not isinstance(result, dict) or result.get("success") is False:
            return ""
        containers = [result]
        for key in ("stream", "result", "data", "value"):
            nested = result.get(key)
            if isinstance(nested, dict):
                containers.append(nested)
        for container in containers:
            value = container.get("stream_id") or container.get("session_id")
            if value:
                return str(value)
        return ""

    async def _open_private_stream(
        self, user_id: str, route_meta: Optional[Dict[str, str]] = None
    ) -> str:
        kwargs: Dict[str, Any] = {"user_id": str(user_id)}
        if route_meta:
            if route_meta.get("account_id"):
                kwargs["account_id"] = route_meta["account_id"]
            if route_meta.get("scope"):
                kwargs["scope"] = route_meta["scope"]
        result = await self.ctx.chat.open_session(
            platform="qq",
            chat_type="private",
            **kwargs,
        )
        return self._extract_stream_id(result)

    def _resolve_manual_period(
        self, period: str
    ) -> Tuple[float, float, datetime, str]:
        now = self._timezone_now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if period == "昨天":
            start = today_start - timedelta(days=1)
            end = today_start
            report_date = start
        else:
            start = today_start
            end = now
            report_date = now
        period_text = f"{start:%Y-%m-%d %H:%M}—{end:%Y-%m-%d %H:%M}"
        return start.timestamp(), end.timestamp(), report_date, period_text

    async def _analyze_event_group(
        self,
        meta: Dict[str, str],
        start_ts: float,
        end_ts: float,
        min_messages: int,
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            **meta,
            "status": "failed",
            "message_count": 0,
            "report": {"overview": "", "events": []},
        }
        messages = await self._get_messages(meta["stream_id"], start_ts, end_ts)
        result["message_count"] = len(messages)
        if len(messages) < max(1, int(min_messages or 10)):
            result["status"] = "insufficient"
            return result

        summary_cfg = self.config.summary
        report = await self._service.analyze_group_event_report(
            messages,
            max_events=max(1, min(20, int(summary_cfg.max_events or 8))),
            max_anchors=(
                max(0, min(5, int(summary_cfg.anchors_per_event or 2)))
                if summary_cfg.include_anchor_quotes
                else 0
            ),
            max_input_messages=max(100, int(summary_cfg.max_input_messages or 1200)),
            include_links=bool(summary_cfg.include_links),
        )
        result["report"] = report
        result["status"] = "ok" if report.get("events") else "empty"
        return result

    async def _deliver_event_reports(
        self,
        destination_stream_id: str,
        groups: List[Dict[str, str]],
        *,
        start_ts: float,
        end_ts: float,
        report_date: datetime,
        period_text: str,
        request_label: str,
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        min_messages = max(1, int(self.config.auto_summary.min_messages or 10))
        group_timeout = max(
            60, int(self.config.advanced.group_timeout_seconds or 300)
        )

        await self._send_text_chunks(
            f"已开始{request_label}：共 {len(groups)} 个群。\n"
            f"统计范围：{period_text}\n"
            "源群将保持静默。",
            destination_stream_id,
        )

        for index, meta in enumerate(groups, start=1):
            await self.ctx.send.text(
                f"正在处理 {index}/{len(groups)}："
                f"{meta['group_name']}（{meta['group_id']}）",
                destination_stream_id,
            )
            try:
                item = await asyncio.wait_for(
                    self._analyze_event_group(
                        meta,
                        start_ts,
                        end_ts,
                        min_messages,
                    ),
                    timeout=group_timeout,
                )
            except asyncio.TimeoutError:
                item = {
                    **meta,
                    "status": "failed",
                    "message_count": 0,
                    "report": {"overview": "", "events": []},
                    "error": f"单群处理超过 {group_timeout} 秒",
                }
            except Exception as exc:
                self.ctx.logger.error(
                    f"群 {meta['group_id']} 事件日报生成失败: {exc}",
                    exc_info=True,
                )
                item = {
                    **meta,
                    "status": "failed",
                    "message_count": 0,
                    "report": {"overview": "", "events": []},
                    "error": str(exc),
                }
            results.append(item)

        index_text = build_daily_index_text(report_date, period_text, results)
        await self._send_text_chunks(index_text, destination_stream_id)

        delivered_groups = 0
        for item in results:
            if item.get("status") != "ok":
                continue
            report = item["report"]
            await self.ctx.send.text(
                f"【{item['group_name']}（{item['group_id']}）】"
                f"共提取 {len(report.get('events') or [])} 个重要事件",
                destination_stream_id,
            )
            images = await self._renderer.generate_event_report_images(
                group_name=item["group_name"],
                group_id=item["group_id"],
                report_date=report_date,
                period_text=period_text,
                message_count=int(item.get("message_count") or 0),
                report=report,
                events_per_page=max(
                    1, min(8, int(self.config.summary.events_per_page or 4))
                ),
            )
            if not images:
                item["status"] = "failed"
                item["error"] = "图片渲染失败"
                await self.ctx.send.text(
                    f"{item['group_name']}（{item['group_id']}）图片渲染失败",
                    destination_stream_id,
                )
                continue

            sent_all = True
            for image_base64 in images:
                sent_all = bool(
                    await self.ctx.send.image(image_base64, destination_stream_id)
                ) and sent_all
            if sent_all:
                delivered_groups += 1
                if self.config.advanced.inject_memory:
                    memory_text = build_event_plain_text(
                        item["group_name"],
                        item["group_id"],
                        report_date,
                        period_text,
                        report,
                    )
                    await self._inject_memory(
                        item["stream_id"],
                        memory_text,
                        "plugin:daily_analysis:event_digest",
                    )
            else:
                item["status"] = "failed"
                item["error"] = "私聊图片发送失败"

        failed = sum(1 for item in results if item.get("status") == "failed")
        empty = sum(
            1
            for item in results
            if item.get("status") in {"empty", "insufficient"}
        )
        await self.ctx.send.text(
            f"{request_label}完成：成功发送 {delivered_groups} 个群，"
            f"无重要内容/消息不足 {empty} 个，失败 {failed} 个。",
            destination_stream_id,
        )
        return results

    async def _run_manual_event_request(
        self,
        destination_stream_id: str,
        target: str,
        period: str,
        guard_key: str,
    ) -> None:
        """处理管理员私聊发起的事件日报请求。"""

        try:
            groups, missing = await self._get_configured_group_streams(target)
            if missing:
                await self.ctx.send.text(
                    "以下群尚未形成可用聊天流：" + "、".join(missing),
                    destination_stream_id,
                )
            if not groups:
                await self.ctx.send.text(
                    "没有可处理的来源群。请先在插件配置中填写群聊白名单，"
                    "并确保机器人已经在群内收到过消息。",
                    destination_stream_id,
                )
                return

            start_ts, end_ts, report_date, period_text = self._resolve_manual_period(
                period
            )
            await self._deliver_event_reports(
                destination_stream_id,
                groups,
                start_ts=start_ts,
                end_ts=end_ts,
                report_date=report_date,
                period_text=period_text,
                request_label=f"手动{period}事件总结",
            )
        except Exception as e:
            self.ctx.logger.error(f"手动事件总结异常: {e}", exc_info=True)
            try:
                await self.ctx.send.text(
                    f"事件总结失败：{type(e).__name__}",
                    destination_stream_id,
                )
            except Exception:
                pass
        finally:
            self._generating.discard(guard_key)

    async def _run_user_summary_in_background(
        self, stream_id: str, user_messages: List[dict], query_user_name: str,
        query_user_id: str, time_range: str, target_date: datetime, guard_key: str,
    ) -> None:
        """后台执行个人总结：分析→渲染→发送。不受宿主对命令处理的 60 秒硬超时限制。"""
        try:
            image_base64, user_summary_text = await self._build_user_summary_image(
                user_messages, query_user_name, query_user_id, target_date
            )
            if not image_base64:
                self.ctx.logger.error(f"用户 {query_user_id} 的个人总结图片渲染失败")
                return
            await self.ctx.send.image(image_base64, stream_id)
            if self.config.advanced.inject_memory and user_summary_text:
                note = f"【关于 {query_user_name}（QQ{query_user_id}）{time_range}的个人总结】{user_summary_text}"
                await self._inject_memory(
                    stream_id, note, f"plugin:daily_analysis:user:{query_user_id}"
                )
        except Exception as e:
            self.ctx.logger.error(f"后台个人总结异常 (用户 {query_user_id}): {e}", exc_info=True)
        finally:
            self._generating.discard(guard_key)

    # ==================== 命令：群聊总结 ====================

    @Command(
        "summary",
        description="私聊生成指定群或全部白名单群的事件日报",
        pattern=r"^/summary(?:\s+(?P<args>.*))?$",
    )
    async def cmd_summary(
        self, stream_id: str = "", group_id: str = "", user_id: str = "", **kwargs: Any
    ) -> Tuple[bool, str, int]:
        try:
            stream_id, group_id, user_id = self._command_identity(
                stream_id,
                group_id,
                user_id,
                kwargs.get("message"),
            )
            if not self.config.plugin.enabled:
                return False, "插件未启用", 0

            # 群聊中的 /summary 只拦截，不调用任何发送能力，确保来源群完全静默。
            if group_id:
                self.ctx.logger.info(
                    f"已静默拦截群 {group_id} 内的 /summary（用户 {user_id}）"
                )
                return True, "群聊内静默拦截", 2

            admin_users = self._as_id_set(self.config.command_permission.admin_users)
            if not admin_users or str(user_id) not in admin_users:
                self.ctx.logger.warning(f"未授权 QQ {user_id} 尝试执行 /summary")
                return True, "未授权请求已静默拦截", 2

            args = ((kwargs.get("matched_groups") or {}).get("args") or "").strip()
            try:
                target, period = parse_summary_command(args)
            except ValueError as exc:
                await self.ctx.send.text(
                    f"{exc}\n"
                    "示例：\n"
                    "/summary 123456789 今天\n"
                    "/summary 123456789 昨天\n"
                    "/summary 全部 今天\n"
                    "/summary 全部 昨天",
                    stream_id,
                )
                return True, "指令格式错误", 2

            allowed_groups = self._as_id_set(self.config.auto_summary.target_chats)
            if not allowed_groups:
                await self.ctx.send.text(
                    "来源群白名单为空，当前禁止生成任何群聊总结。",
                    stream_id,
                )
                return True, "来源群白名单为空", 2
            if target != "全部" and target not in allowed_groups:
                await self.ctx.send.text(
                    f"群 {target} 不在允许总结的群聊名单中。",
                    stream_id,
                )
                return True, "目标群不在白名单", 2

            guard_key = f"event:{stream_id}:{target}:{period}"
            if guard_key in self._generating:
                await self.ctx.send.text("同一份总结仍在生成中，请稍候。", stream_id)
                return True, "重复请求，生成中", 2
            self._generating.add(guard_key)
            try:
                await self.ctx.send.text(
                    f"已接受请求：{target}，{period}。正在读取群聊记录。",
                    stream_id,
                )
                self._spawn_bg(
                    self._run_manual_event_request(
                        stream_id,
                        target,
                        period,
                        guard_key,
                    )
                )
            except Exception:
                self._generating.discard(guard_key)
                raise
            return True, "已开始生成群聊事件日报", 2

        except Exception as e:
            self.ctx.logger.error(f"执行 /summary 出错: {e}", exc_info=True)
            return False, f"执行出错: {e}", 1

    # ==================== 命令：个人总结 ====================

    @Command("mysummary", description="生成个人总结", pattern=r"^/mysummary(?:\s+(?P<args>.*))?$")
    async def cmd_mysummary(
        self, stream_id: str = "", group_id: str = "", user_id: str = "", **kwargs: Any
    ) -> Tuple[bool, str, int]:
        try:
            stream_id, group_id, user_id = self._command_identity(
                stream_id,
                group_id,
                user_id,
                kwargs.get("message"),
            )
            if not self.config.plugin.enabled:
                return False, "插件未启用", 0

            if not group_id:
                return False, "非群聊消息", 0

            if not self._check_group_permission(group_id):
                return False, f"群 {group_id} 无 /mysummary 权限", 0

            if self._is_silent_group(group_id):
                self.ctx.logger.info(f"已静默拦截群 {group_id} 内的 /mysummary")
                return True, "绝对静默群内已拦截", 2

            if not self.config.user_summary.enabled:
                return False, "个人总结功能已关闭", 0

            current_user_id = str(user_id)
            args = ((kwargs.get("matched_groups") or {}).get("args") or "").strip()
            message_dict = kwargs.get("message") or {}

            # 解析时间范围
            time_range = "昨天" if "昨天" in args else "今天"

            # 解析目标用户：优先 @ 消息段，其次纯数字 QQ 号
            target_user_id = ""
            target_user_name = ""
            at_targets = self._extract_at_targets(message_dict)
            if at_targets:
                target_user_id, target_user_name = at_targets[0]
            else:
                for token in args.split():
                    if token.isdigit():
                        target_user_id = token
                        break

            # 决定查看对象与权限（所有人始终可看自己；看他人受名单模式控制）
            allowed_users = self._as_id_set(self.config.user_summary.allowed_users)
            view_mode = self.config.user_summary.view_others_mode
            if target_user_id and target_user_id != current_user_id:
                if allowed_users:
                    in_list = current_user_id in allowed_users
                    # 白名单：不在名单→禁止；黑名单：在名单→禁止
                    denied = (not in_list) if view_mode == "白名单" else in_list
                    if denied:
                        return False, f"用户 {current_user_id} 无查看他人总结权限（{view_mode}）", 0
                query_user_id = target_user_id
                query_user_name = target_user_name or f"用户{target_user_id}"
            else:
                query_user_id = current_user_id
                query_user_name = ""  # 稍后从消息记录补全

            start_ts, end_ts, target_date = self._parse_time_range(time_range)
            if start_ts is None:
                await self.ctx.send.text("只支持查询今天或昨天的记录哦", stream_id)
                return True, f"不支持的时间范围: {args}", 2

            all_messages = await self._get_messages(stream_id, start_ts, end_ts)
            if not all_messages:
                await self.ctx.send.text(f"{time_range}群里没有聊天记录呢", stream_id)
                return True, "没有聊天记录", 2

            user_messages = AnalysisService.filter_user_messages(all_messages, query_user_id)

            # 从消息记录补全用户名
            if user_messages:
                first = user_messages[0]
                query_user_name = (
                    first.get("user_cardname") or first.get("user_nickname") or query_user_name
                )
            if not query_user_name:
                query_user_name = f"用户{query_user_id}"

            is_self = query_user_id == current_user_id

            if not user_messages:
                who = "你" if is_self else query_user_name
                tail = "，多说说话吧~" if is_self else "~"
                await self.ctx.send.text(f"{time_range}{who}没有发言记录呢{tail}", stream_id)
                return True, "用户没有发言记录", 2

            if len(user_messages) < 3:
                if is_self:
                    await self.ctx.send.text(
                        f"{time_range}你只发了{len(user_messages)}条消息，发言太少啦，多聊聊天再来总结吧~",
                        stream_id,
                    )
                else:
                    await self.ctx.send.text(
                        f"{time_range}{query_user_name}只发了{len(user_messages)}条消息，发言太少无法生成总结~",
                        stream_id,
                    )
                return True, "用户发言太少", 2

            guard_key = f"u:{stream_id}:{query_user_id}"
            if guard_key in self._generating:
                await self.ctx.send.text("上一份个人总结还在生成中，请稍候~", stream_id)
                return True, "重复请求，生成中", 2
            self._generating.add(guard_key)
            # 同 cmd_summary：守护键已加但后台任务未接管，send.text 可能抛异常，
            # 失败时必须回收 guard_key，否则该用户命令被永久判为「生成中」。
            try:
                await self.ctx.send.text(
                    f"⏳ 正在分析{query_user_name}的{time_range}发言记录，请稍候...", stream_id
                )
                # 重活放后台执行，命令立即返回，避免宿主对命令处理的 60 秒硬超时把整轮分析掐断
                self._spawn_bg(
                    self._run_user_summary_in_background(
                        stream_id, user_messages, query_user_name, query_user_id,
                        time_range, target_date, guard_key
                    )
                )
            except Exception:
                self._generating.discard(guard_key)  # 后台任务未接管，回收守护键
                raise  # 交外层 except 记日志（exc_info）
            return True, "已开始生成个人总结", 2

        except Exception as e:
            self.ctx.logger.error(f"执行 /mysummary 出错: {e}", exc_info=True)
            return False, f"执行出错: {e}", 1

    @staticmethod
    def _extract_at_targets(message_dict: dict) -> List[Tuple[str, str]]:
        """从消息的 raw_message 段中提取 @ 目标 (user_id, nickname) 列表"""
        targets: List[Tuple[str, str]] = []
        if not isinstance(message_dict, dict):
            return targets
        for seg in message_dict.get("raw_message", []) or []:
            if not isinstance(seg, dict) or seg.get("type") != "at":
                continue
            data = seg.get("data")
            if not isinstance(data, dict):
                continue
            uid = str(data.get("target_user_id") or "")
            name = data.get("target_user_nickname") or data.get("target_user_cardname") or ""
            if uid:
                targets.append((uid, name))
        return targets

    async def _inject_memory(self, stream_id: str, text: str, source_kind: str) -> None:
        """实验性：把总结注入会话上下文，供麦麦记忆系统吸收。失败不影响主流程。"""
        if not text or not stream_id:
            return
        try:
            await self.ctx.maisaka.context.append(
                stream_id=stream_id,
                segments=[{"type": "text", "content": text}],
                visible_text=text[:200],
                source_kind=source_kind,
            )
            self.ctx.logger.info(f"已注入总结到麦麦记忆: {source_kind}")
        except Exception as e:
            self.ctx.logger.warning(f"注入麦麦记忆失败: {e}")

    # ==================== 总结生成（复用逻辑） ====================

    async def _build_user_summary_image(
        self, user_messages: List[dict], user_name: str, user_id: str, target_date: datetime
    ) -> Tuple[Optional[str], Optional[str]]:
        """分析个人各模块并渲染个人总结图片，返回 (base64, summary_text)"""
        service = self._service
        user_stats = service.analyze_single_user_stats(user_messages)

        results = await asyncio.gather(
            service.analyze_single_user_summary(user_messages, user_name, user_id),
            service.analyze_single_user_portrait(user_messages, user_name, user_id),
            service.analyze_single_user_depression(user_messages, user_name, user_id),
            service.analyze_single_user_quotes(user_messages, user_name, user_id),
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, Exception):
                self.ctx.logger.error(f"个人分析子任务异常: {r}", exc_info=r)
        summary_text = results[0] if isinstance(results[0], str) else None
        portrait_data = results[1] if isinstance(results[1], dict) else None
        depression_data = results[2] if isinstance(results[2], dict) else None
        golden_quotes = results[3] if isinstance(results[3], list) else None

        display_order = _slots_to_display_order(
            [
                self.config.user_summary.slot_1,
                self.config.user_summary.slot_2,
                self.config.user_summary.slot_3,
                self.config.user_summary.slot_4,
            ],
            _PERSONAL_MODULE_MAP,
        )

        image_base64 = await self._renderer.generate_user_summary_image(
            user_name=user_name,
            user_id=user_id,
            summary_text=summary_text or "",
            message_count=user_stats["message_count"],
            total_characters=user_stats["char_count"],
            emoji_count=user_stats["emoji_count"],
            hourly_distribution=user_stats["hourly_distribution"],
            user_title=portrait_data.get("title", "") if portrait_data else "",
            user_mbti=portrait_data.get("mbti", "") if portrait_data else "",
            portrait_data=portrait_data,
            depression_data=depression_data,
            golden_quotes=golden_quotes,
            display_order=display_order,
            target_date=target_date,
        )
        return image_base64, summary_text

    # ==================== 定时自动总结 ====================

    def _start_scheduler(self) -> None:
        if self._scheduler_task is not None and not self._scheduler_task.done():
            return
        if not self.config.plugin.enabled or not self.config.auto_summary.enabled:
            return
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        self.ctx.logger.info(
            f"定时总结调度器已启动 - 执行时间: {self.config.auto_summary.time}"
        )

    async def _stop_scheduler(self) -> None:
        task = self._scheduler_task
        self._scheduler_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def _timezone_now(self) -> datetime:
        tz_str = self.config.auto_summary.timezone or "Asia/Shanghai"
        try:
            from zoneinfo import ZoneInfo

            return datetime.now(ZoneInfo(tz_str))
        except Exception as e:
            self.ctx.logger.warning(f"时区 {tz_str} 处理失败，使用系统时间: {e}")
            return datetime.now()

    async def _scheduler_loop(self) -> None:
        while True:
            try:
                now = self._timezone_now()
                time_str = self.config.auto_summary.time or "00:10"
                try:
                    hour, minute = map(int, time_str.split(":"))
                    if not (0 <= hour <= 23 and 0 <= minute <= 59):
                        raise ValueError
                except (TypeError, ValueError):
                    self.ctx.logger.error(f"无效的时间格式: {time_str}，使用 00:10")
                    hour, minute = 0, 10

                today_schedule = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if now >= today_schedule:
                    today_schedule += timedelta(days=1)

                wait_seconds = max(1.0, (today_schedule - now).total_seconds())
                self.ctx.logger.info(
                    f"下次自动总结: {today_schedule.strftime('%Y-%m-%d %H:%M:%S')} "
                    f"(等待 {int(wait_seconds // 3600)}小时{int((wait_seconds % 3600) // 60)}分钟)"
                )
                await asyncio.sleep(wait_seconds)

                current_date = self._timezone_now().date()
                if self._last_auto_date == current_date:
                    continue

                self.ctx.logger.info(f"开始执行自动前一日事件日报 - {current_date}")
                await self._generate_daily_summaries()
                self._last_auto_date = current_date
                self.ctx.logger.info("自动前一日事件日报执行完成")

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.ctx.logger.error(f"定时任务执行出错: {e}", exc_info=True)
                await asyncio.sleep(60)

    async def _generate_daily_summaries(self) -> None:
        """生成前一完整日的白名单群日报，并且只发送到单独配置的管理员私聊。"""

        recipient = str(self.config.auto_summary.recipient_user or "").strip()
        admins = self._as_id_set(self.config.command_permission.admin_users)
        if not recipient:
            self.ctx.logger.warning("自动日报接收账号未配置，本轮不读取群消息、不生成日报")
            return
        if recipient not in admins:
            self.ctx.logger.error(
                f"自动日报接收账号 {recipient} 不在管理员账号列表，本轮不生成"
            )
            return

        configured_groups = self._as_id_set(self.config.auto_summary.target_chats)
        if not configured_groups:
            self.ctx.logger.warning("来源群白名单为空，本轮自动日报不生成")
            return

        try:
            groups, missing = await self._get_configured_group_streams("全部")
        except Exception as exc:
            self.ctx.logger.error(f"获取自动日报来源群失败: {exc}", exc_info=True)
            return
        if missing:
            self.ctx.logger.warning(
                "以下自动日报来源群尚无可用聊天流: " + "、".join(missing)
            )
        if not groups:
            self.ctx.logger.warning("自动日报没有可处理的来源群")
            return

        route_meta = groups[0]
        try:
            destination_stream_id = await self._open_private_stream(
                recipient,
                route_meta=route_meta,
            )
        except Exception as exc:
            self.ctx.logger.error(
                f"打开自动日报接收账号 {recipient} 的 QQ 私聊失败: {exc}",
                exc_info=True,
            )
            return
        if not destination_stream_id:
            self.ctx.logger.error(
                f"宿主未返回自动日报接收账号 {recipient} 的私聊 stream_id"
            )
            return

        # 使用配置时区取前一个完整自然日：[昨日 00:00, 今日 00:00)。
        now = self._timezone_now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday_start = today_start - timedelta(days=1)
        await self._deliver_event_reports(
            destination_stream_id,
            groups,
            start_ts=yesterday_start.timestamp(),
            end_ts=today_start.timestamp(),
            report_date=yesterday_start,
            period_text=(
                f"{yesterday_start:%Y-%m-%d %H:%M}—"
                f"{today_start:%Y-%m-%d %H:%M}"
            ),
            request_label="自动前一日事件日报",
        )


def create_plugin() -> DailyAnalysisPlugin:
    return DailyAnalysisPlugin()
