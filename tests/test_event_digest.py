import unittest
from datetime import datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


_MODULE_PATH = Path(__file__).resolve().parents[1] / "core" / "event_digest.py"
_SPEC = spec_from_file_location("event_digest_under_test", _MODULE_PATH)
event_digest = module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(event_digest)

build_daily_index_text = event_digest.build_daily_index_text
build_event_plain_text = event_digest.build_event_plain_text
build_minor_topic_timeline_text = event_digest.build_minor_topic_timeline_text
build_minor_topic_timeline_pages = event_digest.build_minor_topic_timeline_pages
merge_adjacent_event_candidates = event_digest.merge_adjacent_event_candidates
merge_event_reports_fallback = event_digest.merge_event_reports_fallback
normalize_event_report = event_digest.normalize_event_report
parse_summary_command = event_digest.parse_summary_command
split_message_text = event_digest.split_message_text


class CommandParsingTests(unittest.TestCase):
    def test_accepts_group_and_period(self):
        self.assertEqual(parse_summary_command("123456789 昨天"), ("123456789", "昨天"))
        self.assertEqual(parse_summary_command("全部 今天"), ("全部", "今天"))
        self.assertEqual(parse_summary_command("all"), ("全部", "今天"))

    def test_rejects_missing_or_invalid_target(self):
        for value in ("", "abc 今天", "1234 今天", "123456789 本周", "全部 今天 多余"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse_summary_command(value)


class ReportNormalizationTests(unittest.TestCase):
    def test_sorts_cleans_and_limits_events(self):
        raw = {
            "overview": "  今天   完成了两件事  ",
            "events": [
                {
                    "start_time": "11:30",
                    "end_time": "11:10",
                    "title": " 第二件事 ",
                    "summary": " 明确了   发布安排 ",
                    "outcomes": ["明天发布", "明天发布"],
                    "pending": ["等待确认负责人"],
                    "participants": ["小明", "小红"],
                    "anchors": [
                        {"time": "11:12", "speaker": "小明", "quote": "明天发布"},
                        {"time": "99:00", "speaker": "小红", "quote": "无效时间"},
                    ],
                    "links": ["资料：https://example.com/a。"],
                },
                {
                    "start_time": "09:00",
                    "end_time": "09:20",
                    "title": "第一件事",
                    "summary": "修复了部署错误",
                    "outcomes": [],
                    "pending": [],
                    "participants": ["小李"],
                    "anchors": [],
                    "links": [],
                },
                {
                    "start_time": "25:00",
                    "end_time": "26:00",
                    "title": "无效事件",
                    "summary": "时间不存在",
                },
            ],
        }

        report = normalize_event_report(
            raw,
            max_events=8,
            max_anchors=2,
            include_links=True,
        )

        self.assertEqual(report["overview"], "今天 完成了两件事")
        self.assertEqual([event["title"] for event in report["events"]], ["第一件事", "第二件事"])
        second = report["events"][1]
        self.assertEqual((second["start_time"], second["end_time"]), ("11:10", "11:30"))
        self.assertEqual(second["outcomes"], ["明天发布"])
        self.assertEqual(second["anchors"], [{"time": "11:12", "speaker": "小明", "quote": "明天发布"}])
        self.assertEqual(second["links"], ["https://example.com/a"])

    def test_zero_anchor_and_disabled_links_are_honored(self):
        report = normalize_event_report(
            {
                "events": [
                    {
                        "start_time": "08:00",
                        "end_time": "08:01",
                        "title": "事件",
                        "summary": "内容",
                        "anchors": [{"time": "08:00", "speaker": "甲", "quote": "原话"}],
                        "links": ["https://example.com"],
                    }
                ]
            },
            max_events=8,
            max_anchors=0,
            include_links=False,
        )
        self.assertEqual(report["events"][0]["anchors"], [])
        self.assertEqual(report["events"][0]["links"], [])

    def test_major_and_minor_limits_are_independent(self):
        events = []
        for index, importance in enumerate(
            ["major", "major", "minor", "minor", "minor"],
            start=1,
        ):
            events.append(
                {
                    "importance": importance,
                    "start_time": f"0{index}:00",
                    "end_time": f"0{index}:05",
                    "title": f"事件{index}",
                    "summary": "有效内容",
                }
            )
        report = normalize_event_report(
            {"events": events},
            max_events=1,
            max_minor_events=2,
            max_anchors=0,
        )
        self.assertEqual(
            [event["importance"] for event in report["events"]],
            ["major", "minor", "minor"],
        )

    def test_fallback_merge_is_deterministic(self):
        reports = [
            {
                "overview": "上午处理故障",
                "events": [
                    {
                        "start_time": "09:00",
                        "end_time": "09:30",
                        "title": "排查",
                        "summary": "检查日志",
                    }
                ],
            },
            {
                "overview": "下午恢复服务",
                "events": [
                    {
                        "start_time": "14:00",
                        "end_time": "14:10",
                        "title": "恢复",
                        "summary": "服务恢复",
                    }
                ],
            },
        ]
        merged = merge_event_reports_fallback(
            reports,
            max_events=8,
            max_anchors=2,
            include_links=True,
        )
        self.assertEqual([item["title"] for item in merged["events"]], ["排查", "恢复"])
        self.assertIn("上午处理故障", merged["overview"])
        self.assertIn("下午恢复服务", merged["overview"])

    def test_adjacent_similar_topics_are_merged_locally(self):
        merged = merge_adjacent_event_candidates(
            [
                {
                    "importance": "minor",
                    "start_time": "09:00",
                    "end_time": "09:10",
                    "title": "讨论模型生成速度",
                    "summary": "分享了模型速度测试结果。",
                    "anchors": [],
                },
                {
                    "importance": "minor",
                    "start_time": "09:15",
                    "end_time": "09:25",
                    "title": "讨论模型生成速度",
                    "summary": "继续比较不同模型的生成速度。",
                    "anchors": [],
                },
            ]
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["start_time"], "09:00")
        self.assertEqual(merged[0]["end_time"], "09:25")


class TextOutputTests(unittest.TestCase):
    def setUp(self):
        self.report = {
            "overview": "完成故障处理。",
            "events": [
                {
                    "start_time": "09:00",
                    "end_time": "09:30",
                    "title": "处理故障",
                    "summary": "定位并修复配置问题。",
                    "status": "completed",
                    "facts": ["小明确认配置问题已经修复"],
                    "outcomes": ["服务恢复"],
                    "pending": ["继续观察"],
                    "participants": ["小明"],
                    "anchors": [{"time": "09:22", "speaker": "小明", "quote": "现在恢复了"}],
                    "links": ["https://example.com/log"],
                    "evidence_ids": ["m-001", "m-002"],
                }
            ],
        }

    def test_plain_text_contains_search_anchors(self):
        text = build_event_plain_text(
            "测试群",
            "123456789",
            datetime(2026, 7, 28),
            "2026-07-28 00:00—2026-07-29 00:00",
            self.report,
        )
        self.assertIn("09:00—09:30", text)
        self.assertIn("已完成", text)
        self.assertIn("事实：小明确认配置问题已经修复", text)
        self.assertIn("已确认结果：服务恢复", text)
        self.assertIn("待处理/待确认：继续观察", text)
        self.assertIn("回查：09:22 小明：现在恢复了", text)
        self.assertIn("https://example.com/log", text)
        self.assertIn("证据：已绑定 2 条源消息", text)

    def test_index_lists_all_statuses(self):
        text = build_daily_index_text(
            datetime(2026, 7, 28),
            "2026-07-28 00:00—2026-07-29 00:00",
            [
                {"group_name": "甲群", "group_id": "1", "status": "ok", "report": self.report},
                {"group_name": "乙群", "group_id": "2", "status": "empty", "report": {"events": []}},
                {"group_name": "丙群", "group_id": "3", "status": "insufficient", "report": {"events": []}},
                {"group_name": "丁群", "group_id": "4", "status": "failed", "report": {"events": []}},
            ],
        )
        self.assertIn("09:00 处理故障", text)
        self.assertIn("乙群（2）：无重要事件", text)
        self.assertIn("丙群（3）：消息不足，未生成", text)
        self.assertIn("丁群（4）：生成失败", text)

    def test_long_text_is_split_without_loss(self):
        text = "第一行\n" + ("甲" * 23) + "\n最后一行"
        chunks = split_message_text(text, limit=10)
        self.assertTrue(all(len(chunk) <= 10 for chunk in chunks))
        self.assertEqual("".join(chunks).replace("\n", ""), text.replace("\n", ""))

    def test_minor_topic_timeline_is_timestamped_and_not_limited_by_twelve(self):
        events = []
        for index in range(20):
            hour = 8 + index // 10
            minute = (index % 10) * 5
            events.append(
                {
                    "importance": "minor",
                    "start_time": f"{hour:02d}:{minute:02d}",
                    "end_time": f"{hour:02d}:{minute + 2:02d}",
                    "title": f"普通话题{index + 1}",
                    "summary": f"第{index + 1}个话题的简略记载。",
                    "links": [],
                }
            )
        text = build_minor_topic_timeline_text(
            "测试群",
            "123456789",
            datetime(2026, 7, 28),
            events,
        )
        self.assertIn("共 20 个话题", text)
        self.assertIn("【08:00—08:59】", text)
        self.assertIn("【09:00—09:59】", text)
        self.assertIn("普通话题20", text)


    def test_minor_topic_pages_repeat_identity_and_page_numbers(self):
        events = [
            {
                "importance": "minor",
                "start_time": f"09:{index:02d}",
                "end_time": f"09:{index + 1:02d}",
                "title": f"话题{index}",
                "summary": "这是用于测试分页的一句话概括。",
                "links": [],
            }
            for index in range(20)
        ]
        pages = build_minor_topic_timeline_pages(
            "测试群",
            "123456789",
            datetime(2026, 7, 31),
            events,
            limit=260,
        )
        self.assertGreater(len(pages), 1)
        self.assertTrue(all(len(page) <= 260 for page in pages))
        for index, page in enumerate(pages, start=1):
            self.assertIn(f"其他话题时间线 {index}/{len(pages)}", page)
            self.assertIn("测试群（123456789）", page)


if __name__ == "__main__":
    unittest.main()
