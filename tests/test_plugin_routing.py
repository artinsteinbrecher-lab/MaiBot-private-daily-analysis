import asyncio
import importlib.util
import sys
import types
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace


def _load_plugin_module():
    if importlib.util.find_spec("jinja2") is None:
        jinja2 = types.ModuleType("jinja2")

        class Environment:
            def __init__(self, *args, **kwargs):
                pass

        class FileSystemLoader:
            def __init__(self, *args, **kwargs):
                pass

        jinja2.Environment = Environment
        jinja2.FileSystemLoader = FileSystemLoader
        jinja2.select_autoescape = lambda *args, **kwargs: None
        sys.modules["jinja2"] = jinja2

    sdk = types.ModuleType("maibot_sdk")

    class MaiBotPlugin:
        def __init__(self):
            pass

    class PluginConfigBase:
        pass

    class Command:
        def __init__(self, *args, **kwargs):
            pass

        def __call__(self, function):
            return function

    def Field(*, default=None, default_factory=None, **kwargs):
        return default_factory() if default_factory is not None else default

    sdk.MaiBotPlugin = MaiBotPlugin
    sdk.PluginConfigBase = PluginConfigBase
    sdk.Command = Command
    sdk.Field = Field
    sys.modules["maibot_sdk"] = sdk

    root = Path(__file__).resolve().parents[1]
    package_name = "daily_analysis_plugin_under_test"
    package = types.ModuleType(package_name)
    package.__path__ = [str(root)]
    sys.modules[package_name] = package

    module_name = f"{package_name}.plugin"
    spec = importlib.util.spec_from_file_location(module_name, root / "plugin.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


plugin_module = _load_plugin_module()
DailyAnalysisPlugin = plugin_module.DailyAnalysisPlugin


class _Logger:
    def __init__(self):
        self.messages = []

    def info(self, message, *args, **kwargs):
        self.messages.append(("info", str(message)))

    def warning(self, message, *args, **kwargs):
        self.messages.append(("warning", str(message)))

    def error(self, message, *args, **kwargs):
        self.messages.append(("error", str(message)))


class _Send:
    def __init__(self):
        self.calls = []

    async def text(self, text, stream_id):
        self.calls.append(("text", stream_id, text))
        return True

    async def image(self, image, stream_id):
        self.calls.append(("image", stream_id, image))
        return True


class PluginRoutingTests(unittest.TestCase):
    def _plugin(
        self,
        *,
        recipient="",
        admins=None,
        groups=None,
    ):
        plugin = object.__new__(DailyAnalysisPlugin)
        plugin.ctx = SimpleNamespace(logger=_Logger(), send=_Send(), llm=None)
        plugin.config = SimpleNamespace(
            plugin=SimpleNamespace(enabled=True),
            auto_summary=SimpleNamespace(
                recipient_user=recipient,
                target_chats=groups or [],
            ),
            command_permission=SimpleNamespace(admin_users=admins or []),
            advanced=SimpleNamespace(
                model_task="legacy-task",
                message_interval_seconds=0,
                image_settle_seconds=0,
            ),
        )
        return plugin

    def test_fixed_plugin_tasks_are_used_when_available(self):
        plugin = self._plugin()

        class LLM:
            async def get_available_models(self):
                return [
                    "replyer",
                    "plugin_daily_extract",
                    "plugin_daily_compose",
                    "plugin_daily_verify",
                    "plugin_user_profile",
                ]

        plugin.ctx.llm = LLM()
        routes = asyncio.run(DailyAnalysisPlugin._resolve_plugin_tasks(plugin))
        self.assertEqual(
            routes,
            {
                "main": "replyer",
                "extract": "plugin_daily_extract",
                "compose": "plugin_daily_compose",
                "verify": "plugin_daily_verify",
                "user_profile": "plugin_user_profile",
            },
        )

    def test_missing_plugin_tasks_follow_replyer(self):
        plugin = self._plugin()

        class LLM:
            async def get_available_models(self):
                return ["utils", "replyer"]

        plugin.ctx.llm = LLM()
        routes = asyncio.run(DailyAnalysisPlugin._resolve_plugin_tasks(plugin))
        self.assertEqual(set(routes.values()), {"replyer"})

    def test_missing_replyer_does_not_select_utils_as_a_substitute(self):
        plugin = self._plugin()

        class LLM:
            async def get_available_models(self):
                return ["utils"]

        plugin.ctx.llm = LLM()
        routes = asyncio.run(DailyAnalysisPlugin._resolve_plugin_tasks(plugin))
        self.assertEqual(set(routes.values()), {"replyer"})

    def test_missing_main_task_does_not_select_first_available_task(self):
        plugin = self._plugin()

        class LLM:
            async def get_available_models(self):
                return ["custom-main", "backup"]

        plugin.ctx.llm = LLM()
        routes = asyncio.run(DailyAnalysisPlugin._resolve_plugin_tasks(plugin))
        self.assertEqual(set(routes.values()), {"replyer"})

    def test_empty_task_list_follows_replyer(self):
        plugin = self._plugin()
        plugin.ctx.llm = SimpleNamespace(get_available_models=lambda: asyncio.sleep(0, result=[]))
        routes = asyncio.run(DailyAnalysisPlugin._resolve_plugin_tasks(plugin))
        self.assertEqual(set(routes.values()), {"replyer"})

    def test_legacy_rpc_wrapper_is_supported(self):
        plugin = self._plugin()
        plugin.ctx.llm = SimpleNamespace(
            get_available_models=lambda: asyncio.sleep(
                0,
                result={
                    "models": [
                        "replyer",
                        "plugin_daily_extract",
                        "plugin_user_profile",
                    ]
                },
            ),
        )
        routes = asyncio.run(DailyAnalysisPlugin._resolve_plugin_tasks(plugin))
        self.assertEqual(routes["extract"], "plugin_daily_extract")
        self.assertEqual(routes["compose"], "replyer")
        self.assertEqual(routes["verify"], "replyer")
        self.assertEqual(routes["user_profile"], "plugin_user_profile")

    def test_non_sdk_metadata_is_not_treated_as_registered_tasks(self):
        plugin = self._plugin()

        class LLM:
            async def get_available_models(self):
                return {
                    "models": [
                        {"task": "plugin_daily_extract"},
                        {"name": "plugin_daily_compose"},
                    ]
                }

        plugin.ctx.llm = LLM()
        routes = asyncio.run(DailyAnalysisPlugin._resolve_plugin_tasks(plugin))
        self.assertEqual(set(routes.values()), {"replyer"})

    def test_task_lookup_failure_safely_follows_replyer(self):
        plugin = self._plugin()

        class LLM:
            async def get_available_models(self):
                raise RuntimeError("unavailable")

        plugin.ctx.llm = LLM()
        routes = asyncio.run(DailyAnalysisPlugin._resolve_plugin_tasks(plugin))
        self.assertEqual(set(routes.values()), {"replyer"})

    def test_legacy_model_fields_are_removed_from_webui_schema(self):
        base = plugin_module.MaiBotPlugin
        original = getattr(base, "get_webui_config_schema", None)
        base.get_webui_config_schema = lambda self, **kwargs: {
            "properties": {
                "advanced": {
                    "properties": {
                        "model_task": {},
                        "topic_scan_task": {},
                        "event_refine_task": {},
                        "llm_timeout_seconds": {},
                    }
                }
            },
            "sections": {},
        }
        try:
            plugin = self._plugin()
            schema = DailyAnalysisPlugin.get_webui_config_schema(plugin)
        finally:
            if original is None:
                delattr(base, "get_webui_config_schema")
            else:
                base.get_webui_config_schema = original
        advanced = schema["properties"]["advanced"]["properties"]
        self.assertNotIn("model_task", advanced)
        self.assertNotIn("topic_scan_task", advanced)
        self.assertNotIn("event_refine_task", advanced)
        self.assertIn("llm_timeout_seconds", advanced)

    def test_user_summary_only_runs_fact_portrait(self):
        plugin = self._plugin()
        calls = []

        class Service:
            def analyze_single_user_stats(self, messages):
                return {"message_count": 2, "char_count": 12, "emoji_count": 0}

            async def analyze_single_user_portrait(self, messages, user_name, user_id):
                calls.append((user_name, user_id))
                return {"summary": "事实型概览", "evidence_points": []}

        class Renderer:
            async def generate_user_summary_image(self, **kwargs):
                calls.append(kwargs)
                return "image-base64"

        plugin._service = Service()
        plugin._renderer = Renderer()
        image, summary = asyncio.run(
            DailyAnalysisPlugin._build_user_summary_image(
                plugin,
                [{"processed_plain_text": "测试"}],
                "小明",
                "10001",
                datetime(2026, 8, 2),
            )
        )
        self.assertEqual(image, "image-base64")
        self.assertEqual(summary, "事实型概览")
        self.assertEqual(calls[0], ("小明", "10001"))
        self.assertNotIn("depression_data", calls[1])
        self.assertNotIn("golden_quotes", calls[1])
        self.assertNotIn("display_order", calls[1])

    def test_group_summary_command_never_sends_to_group(self):
        plugin = self._plugin(admins=["10001"], groups=["20001"])
        result = asyncio.run(
            DailyAnalysisPlugin.cmd_summary(
                plugin,
                stream_id="group-stream",
                message={
                    "message_info": {
                        "user_info": {"user_id": "10001"},
                        "group_info": {"group_id": "20001"},
                    }
                },
            )
        )
        self.assertEqual(result, (True, "群聊内静默拦截", 2))
        self.assertEqual(plugin.ctx.send.calls, [])

    def test_auto_job_does_not_read_groups_without_recipient(self):
        plugin = self._plugin(recipient="", admins=["10001"], groups=["20001"])
        called = []

        async def should_not_read(target):
            called.append(target)
            return [], []

        plugin._get_configured_group_streams = should_not_read
        asyncio.run(DailyAnalysisPlugin._generate_daily_summaries(plugin))
        self.assertEqual(called, [])
        self.assertEqual(plugin.ctx.send.calls, [])

    def test_auto_job_routes_once_to_private_destination(self):
        plugin = self._plugin(
            recipient="10001",
            admins=["10001", "10002"],
            groups=["20001", "20002"],
        )
        groups = [
            {
                "group_id": "20001",
                "group_name": "来源群",
                "stream_id": "source-stream",
                "account_id": "bot-account",
                "scope": "primary",
            }
        ]
        observed = {}

        async def get_groups(target):
            self.assertEqual(target, "全部")
            return groups, ["20002"]

        async def open_private(user_id, route_meta=None):
            observed["open"] = (user_id, route_meta)
            return "private-stream"

        async def deliver(destination_stream_id, selected_groups, **kwargs):
            observed["deliver"] = (destination_stream_id, selected_groups, kwargs)
            return []

        plugin._get_configured_group_streams = get_groups
        plugin._open_private_stream = open_private
        plugin._deliver_event_reports = deliver
        plugin._timezone_now = lambda: datetime(2026, 7, 29, 0, 10)

        asyncio.run(DailyAnalysisPlugin._generate_daily_summaries(plugin))

        self.assertEqual(observed["open"], ("10001", groups[0]))
        destination, selected, kwargs = observed["deliver"]
        self.assertEqual(destination, "private-stream")
        self.assertEqual(selected, groups)
        self.assertEqual(kwargs["request_label"], "自动前一日事件日报")
        self.assertEqual(
            datetime.fromtimestamp(kwargs["start_ts"]),
            datetime(2026, 7, 28, 0, 0),
        )
        self.assertEqual(
            datetime.fromtimestamp(kwargs["end_ts"]),
            datetime(2026, 7, 29, 0, 0),
        )
        self.assertEqual(plugin.ctx.send.calls, [])

    def test_partial_report_without_events_keeps_partial_status(self):
        plugin = self._plugin()
        plugin.config.summary = SimpleNamespace(
            coverage_mode="完整覆盖",
            detail_level="完整",
            max_events=12,
            max_minor_events=30,
            anchors_per_event=2,
            include_anchor_quotes=True,
            max_input_messages=1200,
            include_links=True,
        )
        plugin.config.advanced = SimpleNamespace(
            event_chunk_messages=120,
            event_chunk_characters=8000,
            event_retry_count=2,
            split_chunk_on_failure=True,
        )

        async def get_messages(*args):
            return [
                {
                    "processed_plain_text": "一条有效消息",
                    "time": 1.0,
                }
            ]

        class Service:
            async def analyze_group_event_report(self, messages, **kwargs):
                return {
                    "overview": "",
                    "events": [],
                    "partial": True,
                    "coverage": {
                        "total_messages": 1,
                        "analyzed_messages": 0,
                        "coverage_percent": 0.0,
                        "failed_ranges": [
                            {
                                "start_time": "00:00",
                                "end_time": "00:00",
                                "message_count": 1,
                            }
                        ],
                    },
                }

        plugin._get_messages = get_messages
        plugin._service = Service()
        result = asyncio.run(
            DailyAnalysisPlugin._analyze_event_group(
                plugin,
                {
                    "group_id": "20001",
                    "group_name": "来源群",
                    "stream_id": "source-stream",
                },
                0,
                2,
                1,
            )
        )
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["report"]["events"], [])

    def test_minor_only_report_is_sent_as_text_without_image_rendering(self):
        plugin = self._plugin()
        plugin.config.auto_summary.min_messages = 1
        plugin.config.summary = SimpleNamespace(events_per_page=4)
        plugin.config.advanced = SimpleNamespace(
            group_timeout_seconds=60,
            inject_memory=False,
            message_interval_seconds=0,
            image_settle_seconds=0,
        )

        async def analyze(meta, *args):
            return {
                **meta,
                "status": "ok",
                "message_count": 20,
                "report": {
                    "overview": "普通话题概览",
                    "events": [
                        {
                            "importance": "minor",
                            "start_time": "09:00",
                            "end_time": "09:15",
                            "title": "讨论部署方式",
                            "summary": "简单交流了 Docker 配置。",
                            "outcomes": [],
                            "pending": [],
                            "participants": [],
                            "anchors": [],
                            "links": [],
                        }
                    ],
                    "coverage": {
                        "total_messages": 20,
                        "analyzed_messages": 20,
                        "coverage_percent": 100.0,
                    },
                    "refinement": {
                        "requested": 0,
                        "refined": 0,
                        "fallback": 0,
                    },
                },
            }

        class Renderer:
            async def generate_event_report_images(self, **kwargs):
                raise AssertionError("普通话题不应进入图片渲染")

        plugin._analyze_event_group = analyze
        plugin._renderer = Renderer()
        results = asyncio.run(
            DailyAnalysisPlugin._deliver_event_reports(
                plugin,
                "private-stream",
                [
                    {
                        "group_id": "20001",
                        "group_name": "来源群",
                        "stream_id": "source-stream",
                    }
                ],
                start_ts=0,
                end_ts=1,
                report_date=datetime(2026, 7, 30),
                period_text="2026-07-30 00:00—2026-07-30 23:59",
                request_label="测试日报",
            )
        )
        self.assertEqual(results[0]["status"], "ok")
        self.assertFalse(
            any(call[0] == "image" for call in plugin.ctx.send.calls)
        )
        sent_text = "\n".join(
            call[2] for call in plugin.ctx.send.calls if call[0] == "text"
        )
        self.assertIn("其他话题时间线", sent_text)
        self.assertIn("09:00—09:15｜讨论部署方式", sent_text)


    def test_image_send_failure_falls_back_to_major_text(self):
        plugin = self._plugin()
        plugin.config.auto_summary.min_messages = 1
        plugin.config.summary = SimpleNamespace(events_per_page=4)
        plugin.config.advanced = SimpleNamespace(
            group_timeout_seconds=60,
            inject_memory=False,
            message_interval_seconds=0,
            image_settle_seconds=0,
        )

        async def analyze(meta, *args):
            return {
                **meta,
                "status": "ok",
                "message_count": 30,
                "report": {
                    "overview": "主要事件概览",
                    "events": [
                        {
                            "importance": "major",
                            "start_time": "10:00",
                            "end_time": "10:30",
                            "title": "处理接口故障",
                            "summary": "群友排查并恢复了接口。",
                            "outcomes": ["接口恢复"],
                            "pending": [],
                            "participants": ["群友"],
                            "anchors": [
                                {
                                    "time": "10:20",
                                    "speaker": "群友",
                                    "quote": "现在恢复了",
                                }
                            ],
                            "links": [],
                        }
                    ],
                    "coverage": {
                        "total_messages": 30,
                        "analyzed_messages": 30,
                        "coverage_percent": 100.0,
                    },
                    "refinement": {
                        "requested": 1,
                        "refined": 1,
                        "fallback": 0,
                    },
                },
            }

        class Renderer:
            async def generate_event_report_images(self, **kwargs):
                return ["image-base64"]

        async def uncertain_image(image, stream_id):
            plugin.ctx.send.calls.append(("image", stream_id, image))
            return False

        plugin._analyze_event_group = analyze
        plugin._renderer = Renderer()
        plugin.ctx.send.image = uncertain_image
        results = asyncio.run(
            DailyAnalysisPlugin._deliver_event_reports(
                plugin,
                "private-stream",
                [
                    {
                        "group_id": "20001",
                        "group_name": "来源群",
                        "stream_id": "source-stream",
                    }
                ],
                start_ts=0,
                end_ts=1,
                report_date=datetime(2026, 7, 31),
                period_text="2026-07-31 00:00—2026-07-31 23:59",
                request_label="测试日报",
            )
        )
        self.assertEqual(results[0]["status"], "partial")
        sent_text = "\n".join(
            call[2] for call in plugin.ctx.send.calls if call[0] == "text"
        )
        self.assertIn("补发可搜索文字版", sent_text)
        self.assertIn("处理接口故障", sent_text)
        self.assertIn("部分完成 1 个", sent_text)
        self.assertIn("失败 0 个", sent_text)

    def test_report_messages_use_configured_pacing(self):
        plugin = self._plugin()
        plugin.config.advanced.message_interval_seconds = 1.25
        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        original_sleep = plugin_module.asyncio.sleep
        plugin_module.asyncio.sleep = fake_sleep
        try:
            result = asyncio.run(
                DailyAnalysisPlugin._send_report_text(
                    plugin,
                    "第 1/2 页",
                    "private-stream",
                )
            )
        finally:
            plugin_module.asyncio.sleep = original_sleep

        self.assertTrue(result)
        self.assertEqual(sleeps, [1.25])
        self.assertEqual(
            plugin.ctx.send.calls,
            [("text", "private-stream", "第 1/2 页")],
        )

    def test_same_destination_reports_are_serialized_as_whole_jobs(self):
        plugin = self._plugin()
        timeline = []
        active = 0
        max_active = 0

        async def locked_delivery(destination_stream_id, groups, **kwargs):
            nonlocal active, max_active
            label = kwargs["request_label"]
            active += 1
            max_active = max(max_active, active)
            timeline.append(("start", label))
            await asyncio.sleep(0.01)
            timeline.append(("end", label))
            active -= 1
            return []

        plugin._deliver_event_reports_locked = locked_delivery

        async def run_two():
            common = {
                "start_ts": 0,
                "end_ts": 1,
                "report_date": datetime(2026, 8, 16),
                "period_text": "测试",
            }
            first = asyncio.create_task(
                DailyAnalysisPlugin._deliver_event_reports(
                    plugin,
                    "private-stream",
                    [],
                    request_label="日报 A",
                    **common,
                )
            )
            await asyncio.sleep(0)
            second = asyncio.create_task(
                DailyAnalysisPlugin._deliver_event_reports(
                    plugin,
                    "private-stream",
                    [],
                    request_label="日报 B",
                    **common,
                )
            )
            await asyncio.gather(first, second)

        asyncio.run(run_two())
        self.assertEqual(max_active, 1)
        self.assertEqual(
            timeline,
            [
                ("start", "日报 A"),
                ("end", "日报 A"),
                ("start", "日报 B"),
                ("end", "日报 B"),
            ],
        )

    def test_send_result_compatibility_handles_nested_sdk_payloads(self):
        self.assertTrue(DailyAnalysisPlugin._send_succeeded(True))
        self.assertTrue(
            DailyAnalysisPlugin._send_succeeded({"success": {"success": True}})
        )
        self.assertFalse(DailyAnalysisPlugin._send_succeeded({"success": False}))
        self.assertFalse(DailyAnalysisPlugin._send_succeeded(None))


if __name__ == "__main__":
    unittest.main()
