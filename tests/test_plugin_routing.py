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
        plugin.ctx = SimpleNamespace(logger=_Logger(), send=_Send())
        plugin.config = SimpleNamespace(
            plugin=SimpleNamespace(enabled=True),
            auto_summary=SimpleNamespace(
                recipient_user=recipient,
                target_chats=groups or [],
            ),
            command_permission=SimpleNamespace(admin_users=admins or []),
        )
        return plugin

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


if __name__ == "__main__":
    unittest.main()
