import asyncio
import json
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
        message_id = self.service._event_message_id(self.messages[0])
        self.assertIn(f"[09:30:20] [id={message_id}] 小明:", formatted)

    def test_llm_timeout_is_forwarded_to_capability_rpc(self):
        calls = []

        class LLM:
            async def generate(self, prompt, **kwargs):
                calls.append((prompt, kwargs))
                return {"success": True, "response": "ok"}

        context = types.SimpleNamespace(logger=_Logger(), llm=LLM())
        service = AnalysisService(context, call_timeout_s=180)
        response = asyncio.run(
            service._llm("test", request_type="timeout-forwarding")
        )

        self.assertEqual(response, "ok")
        self.assertEqual(calls[0][1]["rpc_timeout_ms"], 180000)

    def test_claim_cannot_use_unrelated_real_evidence_id(self):
        message_id = self.service._event_message_id(self.messages[0])
        report = self.service._ground_event_report(
            {
                "events": [
                    {
                        "title": "虚假声明",
                        "facts": [
                            {
                                "claim": "数据库已被永久删除",
                                "evidence_ids": [message_id],
                            }
                        ],
                    }
                ]
            },
            self.messages,
            max_events=8,
            max_anchors=2,
            include_links=True,
        )
        self.assertEqual(report["events"], [])

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
                refine_major_events=False,
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
                refine_major_events=False,
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
                refine_major_events=False,
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
                refine_major_events=False,
            )
        )
        self.assertTrue(report["partial"])
        self.assertEqual(report["coverage"]["chunks_failed"], 1)
        self.assertEqual(report["coverage"]["coverage_percent"], 0.0)
        self.assertEqual(len(report["coverage"]["failed_ranges"]), 1)

    def test_refinement_failure_keeps_compact_major_candidate(self):
        event = self._fake_chunk_report(
            self._bulk_messages(20),
            importance="major",
        )["events"][0]

        async def fail_llm(*args, **kwargs):
            return None

        self.service._llm = fail_llm
        report = asyncio.run(
            self.service._refine_major_events(
                {"overview": "", "events": [event]},
                self._bulk_messages(20),
                max_anchors=2,
                include_links=True,
                detail_level="full",
            )
        )
        self.assertEqual(report["events"][0]["summary"], "群内讨论了有效内容。")
        self.assertEqual(report["refinement"]["requested"], 1)
        self.assertEqual(report["refinement"]["refined"], 0)
        self.assertEqual(report["refinement"]["fallback"], 1)

    def _refine_candidates_pair(self):
        messages = self._bulk_messages(20)
        base = self._fake_chunk_report(messages, importance="major")["events"][0]
        candidate_a = {**base, "event_id": "cand-1"}
        candidate_b = {**base, "title": "另一个事件", "event_id": "cand-2"}
        return messages, candidate_a, candidate_b

    @staticmethod
    def _single_candidate_response():
        return json.dumps(
            {
                "events": [
                    {
                        "candidate_id": "E1",
                        "importance": "major",
                        "status": "discussed",
                        "start_time": "08:00",
                        "end_time": "08:00",
                        "title": "详细事件",
                        "summary": "详细经过",
                        "facts": [{"claim": "有效消息 0", "evidence_ids": []}],
                        "outcomes": [],
                        "pending": [],
                        "participants": ["群友"],
                        "anchors": [],
                        "links": [],
                    }
                ]
            },
            ensure_ascii=False,
        )

    def test_truncated_refine_batch_retries_each_candidate_individually(self):
        messages, candidate_a, candidate_b = self._refine_candidates_pair()
        prompts = []

        async def truncation_llm(prompt, **kwargs):
            prompts.append(prompt)
            # 多事件批量输出被 MAX_TOKENS 截断 → 整批解析失败；
            # 单事件提示词（不含 ### E2）可以完整返回。
            if "### E2" in prompt:
                return None
            return self._single_candidate_response()

        self.service._llm = truncation_llm
        report = asyncio.run(
            self.service._refine_major_events(
                {"overview": "", "events": [candidate_a, candidate_b]},
                messages,
                max_anchors=2,
                include_links=True,
                detail_level="full",
            )
        )
        self.assertEqual(len(prompts), 3)  # 1 次批量 + 2 次单事件重试
        self.assertEqual(report["refinement"]["requested"], 2)
        self.assertEqual(report["refinement"]["refined"], 2)
        self.assertEqual(report["refinement"]["fallback"], 0)
        self.assertEqual(
            sorted(event["event_id"] for event in report["events"]),
            ["cand-1", "cand-2"],
        )
        self.assertTrue(all(event.get("refined") for event in report["events"]))

    def test_refine_batch_size_one_sends_single_candidate_prompts(self):
        messages, candidate_a, candidate_b = self._refine_candidates_pair()
        prompts = []

        async def single_llm(prompt, **kwargs):
            prompts.append(prompt)
            self.assertNotIn("### E2", prompt)
            return self._single_candidate_response()

        self.service._llm = single_llm
        report = asyncio.run(
            self.service._refine_major_events(
                {"overview": "", "events": [candidate_a, candidate_b]},
                messages,
                max_anchors=2,
                include_links=True,
                detail_level="full",
                refine_batch_size=1,
            )
        )
        self.assertEqual(len(prompts), 2)
        self.assertEqual(report["refinement"]["refined"], 2)

    def test_refinement_grounds_detailed_major_event(self):
        messages = self._bulk_messages(20)
        messages[0]["processed_plain_text"] = "有效消息 0，服务已经确认恢复，后续继续观察"
        start_time = self.service._event_datetime(messages[0]["time"]).strftime(
            "%H:%M"
        )
        message_id = self.service._event_message_id(messages[0])
        candidate = self._fake_chunk_report(messages, importance="major")[
            "events"
        ][0]
        candidate["event_id"] = "candidate-event-1"

        async def refined_llm(*args, **kwargs):
            return json.dumps(
                {
                    "events": [
                        {
                            "candidate_id": "E1",
                            "importance": "major",
                            "status": "completed",
                            "start_time": start_time,
                            "end_time": start_time,
                            "title": "详细事件",
                            "summary": "服务已经确认恢复，后续继续观察。",
                            "facts": [
                                {"claim": "服务已经确认恢复", "evidence_ids": [message_id]}
                            ],
                            "outcomes": [
                                {"claim": "服务已经确认恢复", "evidence_ids": [message_id]}
                            ],
                            "pending": [
                                {"claim": "后续继续观察", "evidence_ids": [message_id]}
                            ],
                            "participants": ["群友"],
                            "anchors": [
                                {
                                    "time": start_time,
                                    "speaker": "群友",
                                    "quote": "有效消息 0",
                                }
                            ],
                            "links": [],
                        }
                    ]
                },
                ensure_ascii=False,
            )

        self.service._llm = refined_llm
        report = asyncio.run(
            self.service._refine_major_events(
                {"overview": "", "events": [candidate]},
                messages,
                max_anchors=2,
                include_links=True,
                detail_level="full",
            )
        )
        event = report["events"][0]
        self.assertTrue(event["refined"])
        self.assertEqual(event["event_id"], "candidate-event-1")
        self.assertEqual(event["title"], "详细事件")
        self.assertEqual(event["outcomes"], ["服务已经确认恢复"])
        self.assertEqual(event["pending"], ["后续继续观察"])
        self.assertEqual(report["refinement"]["refined"], 1)

    def test_verifier_can_only_remove_claims_and_downgrade_status(self):
        event = {
            "event_id": "evt-1",
            "importance": "major",
            "status": "completed",
            "facts": ["服务恢复了", "不存在的结论"],
            "fact_bindings": [
                {"claim": "服务恢复了", "evidence_ids": ["m-1"]},
                {"claim": "不存在的结论", "evidence_ids": ["m-1"]},
            ],
            "outcomes": [],
            "outcome_bindings": [],
            "pending": [],
            "pending_bindings": [],
        }

        async def verify_llm(*args, **kwargs):
            return json.dumps(
                {
                    "events": [
                        {
                            "event_id": "evt-1",
                            "status": "discussed",
                            "unsupported": {
                                "facts": ["不存在的结论", "模型新增声明"],
                                "outcomes": [],
                                "pending": [],
                            },
                            "facts": ["模型新增声明"],
                        }
                    ]
                },
                ensure_ascii=False,
            )

        self.service._llm = verify_llm
        verified, count = asyncio.run(
            self.service._verify_major_batch([event], self.messages)
        )
        self.assertEqual(count, 1)
        self.assertEqual(verified[0]["facts"], ["服务恢复了"])
        self.assertEqual(verified[0]["status"], "discussed")

    def test_verifier_failure_keeps_locally_grounded_event(self):
        event = {
            "event_id": "evt-1",
            "importance": "major",
            "status": "discussed",
            "facts": ["服务恢复了"],
            "fact_bindings": [{"claim": "服务恢复了", "evidence_ids": ["m-1"]}],
            "outcomes": [],
            "outcome_bindings": [],
            "pending": [],
            "pending_bindings": [],
        }

        async def fail_llm(*args, **kwargs):
            return None

        self.service._llm = fail_llm
        verified, count = asyncio.run(
            self.service._verify_major_batch([event], self.messages)
        )
        self.assertEqual(count, 0)
        self.assertEqual(verified, [event])

    def test_user_portrait_uses_dedicated_model_and_filters_fake_quote(self):
        calls = []

        class LLM:
            async def generate(self, prompt, **kwargs):
                calls.append(kwargs.get("model"))
                return {
                    "success": True,
                    "response": json.dumps(
                        {
                            "name": "小明",
                            "summary": "样本中讨论了服务恢复。",
                            "topics": ["服务恢复"],
                            "communication_style": "以简短陈述为主。",
                            "evidence_points": [
                                {"quote": "服务恢复了", "observation": "报告服务已恢复"},
                                {"quote": "并不存在的原话", "observation": "无效"},
                            ],
                        },
                        ensure_ascii=False,
                    ),
                }

        self.service.ctx.llm = LLM()
        self.service.user_profile_model = "plugin_user_profile"
        profile = asyncio.run(
            self.service.analyze_single_user_portrait(
                self.messages * 3, "小明", "10001"
            )
        )
        self.assertEqual(calls, ["plugin_user_profile"])
        self.assertEqual(len(profile["evidence_points"]), 1)
        self.assertEqual(profile["evidence_points"][0]["quote"], "服务恢复了")

    def test_topic_and_refine_calls_use_separate_model_tasks(self):
        self.service.topic_model = "daily_topic_scan"
        self.service.refine_model = "daily_event_refine"
        calls = []

        class LLM:
            async def generate(self, prompt, **kwargs):
                calls.append(kwargs.get("model"))
                if kwargs.get("model") == "daily_topic_scan":
                    return {"success": True, "response": '{"events":[]}'}
                return {
                    "success": True,
                    "response": '{"events":[{"candidate_id":"E1",'
                    '"importance":"major","start_time":"08:00",'
                    '"end_time":"08:00","title":"事件",'
                    '"summary":"详细经过","outcomes":[],"pending":[],'
                    '"participants":["群友"],"anchors":[],"links":[]}]}',
                }

        self.service.ctx.llm = LLM()
        messages = self._bulk_messages(5)
        asyncio.run(
            self.service._extract_event_chunk(
                messages,
                max_anchors=1,
                include_links=True,
                detail_level="standard",
                chunk_label="1",
                chunk_count=1,
            )
        )
        candidate = self._fake_chunk_report(messages, importance="major")["events"][0]
        asyncio.run(
            self.service._refine_major_batch(
                [candidate],
                messages,
                max_anchors=1,
                include_links=True,
                detail_level="full",
            )
        )
        self.assertEqual(calls, ["daily_topic_scan", "daily_event_refine"])


if __name__ == "__main__":
    unittest.main()
