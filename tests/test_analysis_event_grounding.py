import asyncio
import sys
import types
import unittest
from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE_NAME = "daily_analysis_core_under_test"
_PACKAGE = types.ModuleType(_PACKAGE_NAME)
_PACKAGE.__path__ = [str(_ROOT / "core")]
sys.modules[_PACKAGE_NAME] = _PACKAGE


def _load_core_module(module_name):
    qualified_name = f"{_PACKAGE_NAME}.{module_name}"
    spec = spec_from_file_location(
        qualified_name,
        _ROOT / "core" / f"{module_name}.py",
    )
    module = module_from_spec(spec)
    sys.modules[qualified_name] = module
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


_load_core_module("constants")
_load_core_module("event_digest")
analysis_module = _load_core_module("analysis")
AnalysisService = analysis_module.AnalysisService


class _Logger:
    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class _Context:
    logger = _Logger()


class EventGroundingTests(unittest.TestCase):
    def setUp(self):
        self.service = AnalysisService(
            _Context(),
            timezone_name="Asia/Shanghai",
        )
        timestamp = datetime(2026, 7, 28, 1, 30, 20, tzinfo=timezone.utc).timestamp()
        self.messages = [
            {
                "time": timestamp,
                "user_nickname": "小明",
                "user_cardname": "",
                "processed_plain_text": "服务恢复了，日志在 https://example.com/log",
            }
        ]

    def test_event_time_uses_configured_timezone(self):
        formatted = self.service._format_event_messages(self.messages)
        self.assertIn("[09:30:20] 小明:", formatted)

    def test_report_fields_are_grounded_in_source_messages(self):
        report = self.service._ground_event_report(
            {
                "overview": "故障已经处理。",
                "events": [
                    {
                        "start_time": "09:29",
                        "end_time": "09:40",
                        "title": "恢复服务",
                        "summary": "服务恢复。",
                        "participants": ["小明", "不存在的人"],
                        "anchors": [
                            {"time": "09:30", "speaker": "小明", "quote": "服务恢复了"},
                            {"time": "09:30", "speaker": "小明", "quote": "并不存在的原话"},
                        ],
                        "links": [
                            "https://example.com/log",
                            "https://hallucinated.invalid",
                        ],
                    }
                ],
            },
            self.messages,
            max_events=8,
            max_anchors=2,
            include_links=True,
        )
        event = report["events"][0]
        self.assertEqual((event["start_time"], event["end_time"]), ("09:30", "09:30"))
        self.assertEqual(event["participants"], ["小明"])
        self.assertEqual(
            event["anchors"],
            [{"time": "09:30", "speaker": "小明", "quote": "服务恢复了"}],
        )
        self.assertEqual(event["links"], ["https://example.com/log"])

    def test_balanced_sampling_keeps_day_edges(self):
        messages = [{"time": index} for index in range(100)]
        selected = self.service._select_time_balanced_messages(messages, 10)
        self.assertEqual(len(selected), 10)
        self.assertEqual(selected[0]["time"], 0)
        self.assertEqual(selected[-1]["time"], 99)

    def test_balanced_sampling_preserves_quiet_hours(self):
        busy_start = datetime(2026, 7, 28, 1, 0, tzinfo=timezone.utc).timestamp()
        quiet_time = datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc).timestamp()
        messages = [{"time": busy_start + index} for index in range(100)]
        messages.append({"time": quiet_time, "marker": "quiet-hour"})
        selected = self.service._select_time_balanced_messages(messages, 10)
        self.assertEqual(len(selected), 10)
        self.assertTrue(any(item.get("marker") == "quiet-hour" for item in selected))

    def _bulk_messages(self, count):
        start = datetime(2026, 7, 28, 0, 0, tzinfo=timezone.utc).timestamp()
        return [
            {
                "time": start + index,
                "user_nickname": "群友",
                "user_cardname": "",
                "processed_plain_text": f"有效消息 {index}",
                "is_command": False,
                "is_notify": False,
            }
            for index in range(count)
        ]

    def _fake_chunk_report(self, messages, importance="major"):
        start_time = self.service._event_datetime(messages[0]["time"]).strftime("%H:%M")
        end_time = self.service._event_datetime(messages[-1]["time"]).strftime("%H:%M")
        return {
            "overview": f"{start_time}时段动态",
            "events": [
                {
                    "importance": importance,
                    "start_time": start_time,
                    "end_time": end_time,
                    "title": f"{start_time}时段",
                    "summary": "群内讨论了有效内容。",
                    "outcomes": [],
                    "pending": [],
                    "participants": ["群友"],
                    "anchors": [],
                    "links": [],
                }
            ],
        }

    def test_full_coverage_accounts_for_all_3000_messages(self):
        async def fake_extract(messages, **kwargs):
            return self._fake_chunk_report(messages)

        self.service._extract_event_chunk = fake_extract
        report = asyncio.run(
            self.service.analyze_group_event_report(
                self._bulk_messages(3000),
                max_events=30,
                max_minor_events=30,
                coverage_mode="full",
                detail_level="full",
                chunk_messages=120,
                chunk_characters=30000,
                retry_count=0,
            )
        )
        coverage = report["coverage"]
        self.assertEqual(coverage["total_messages"], 3000)
        self.assertEqual(coverage["analyzed_messages"], 3000)
        self.assertEqual(coverage["chunks_total"], 25)
        self.assertEqual(coverage["chunks_failed"], 0)
        self.assertEqual(coverage["coverage_percent"], 100.0)
        self.assertFalse(report["sampled"])
        self.assertFalse(report["partial"])

    def test_failed_chunk_is_retried_before_succeeding(self):
        calls = 0

        async def flaky_extract(messages, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return None
            return self._fake_chunk_report(messages)

        self.service._extract_event_chunk = flaky_extract
        report = asyncio.run(
            self.service.analyze_group_event_report(
                self._bulk_messages(30),
                max_events=12,
                max_minor_events=30,
                coverage_mode="full",
                retry_count=1,
            )
        )
        self.assertEqual(calls, 2)
        self.assertEqual(report["coverage"]["retry_success"], 1)
        self.assertEqual(report["coverage"]["coverage_percent"], 100.0)
        self.assertFalse(report["partial"])

    def test_failed_chunk_is_split_and_both_halves_are_preserved(self):
        calls = []

        async def size_sensitive_extract(messages, **kwargs):
            calls.append(len(messages))
            if len(messages) > 40:
                return None
            return self._fake_chunk_report(messages, importance="minor")

        self.service._extract_event_chunk = size_sensitive_extract
        report = asyncio.run(
            self.service.analyze_group_event_report(
                self._bulk_messages(80),
                max_events=12,
                max_minor_events=30,
                coverage_mode="full",
                retry_count=0,
                split_on_timeout=True,
                chunk_messages=120,
                chunk_characters=30000,
            )
        )
        self.assertEqual(calls, [80, 40, 40])
        self.assertEqual(report["coverage"]["chunks_total"], 2)
        self.assertEqual(report["coverage"]["split_chunks"], 2)
        self.assertEqual(report["coverage"]["analyzed_messages"], 80)
        self.assertEqual(report["coverage"]["coverage_percent"], 100.0)
        self.assertFalse(report["partial"])

    def test_unrecoverable_chunk_marks_report_partial_with_failed_range(self):
        async def always_fail(messages, **kwargs):
            return None

        self.service._extract_event_chunk = always_fail
        report = asyncio.run(
            self.service.analyze_group_event_report(
                self._bulk_messages(30),
                coverage_mode="full",
                retry_count=0,
                split_on_timeout=True,
            )
        )
        self.assertTrue(report["partial"])
        self.assertEqual(report["coverage"]["chunks_failed"], 1)
        self.assertEqual(report["coverage"]["coverage_percent"], 0.0)
        self.assertEqual(len(report["coverage"]["failed_ranges"]), 1)


if __name__ == "__main__":
    unittest.main()
