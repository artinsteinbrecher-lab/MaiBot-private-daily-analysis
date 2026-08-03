import asyncio
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

try:
    from core.rendering import SummaryRenderer
except ModuleNotFoundError as exc:  # optional host dependency in the local test image
    if exc.name != "jinja2":
        raise
    SummaryRenderer = None


class _Logger:
    def __init__(self):
        self.messages = []

    def error(self, message, *args, **kwargs):
        self.messages.append(str(message))

    def warning(self, message, *args, **kwargs):
        self.messages.append(str(message))


class FactRenderingTests(unittest.TestCase):
    @unittest.skipIf(SummaryRenderer is None, "jinja2 is not installed in the test environment")
    def _renderer(self):
        return SummaryRenderer(SimpleNamespace(logger=_Logger()))

    @unittest.skipIf(SummaryRenderer is None, "jinja2 is not installed in the test environment")
    def test_event_template_renders_fact_brief_sections_and_ignores_entertainment_data(self):
        renderer = self._renderer()
        captured = []

        async def render_png(html):
            captured.append(html)
            return "rendered"

        renderer._render_png_base64 = render_png
        report = {
            "overview": "FACT_OVERVIEW",
            "coverage": {"total_messages": 10, "analyzed_messages": 10, "coverage_percent": 100},
            "events": [
                {
                    "importance": "major",
                    "status": "completed",
                    "start_time": "09:00",
                    "end_time": "09:10",
                    "title": "FACT_EVENT",
                    "summary": "FACT_SUMMARY",
                    "facts": ["FACT_CLAIM"],
                    "outcomes": ["FACT_OUTCOME"],
                    "pending": ["FACT_PENDING"],
                    "participants": ["Alice"],
                    "anchors": [{"time": "09:05", "speaker": "Alice", "quote": "FACT_QUOTE"}],
                    "evidence_ids": ["m-1"],
                    "links": ["https://example.test/fact"],
                    "mbti": "ENTERTAINMENT_SHOULD_NOT_RENDER",
                    "depression_index": "ENTERTAINMENT_SHOULD_NOT_RENDER",
                    "golden_quote": "ENTERTAINMENT_SHOULD_NOT_RENDER",
                    "ranking": "ENTERTAINMENT_SHOULD_NOT_RENDER",
                }
            ],
        }
        images = asyncio.run(
            renderer.generate_event_report_images(
                group_name="Group",
                group_id="1",
                report_date=datetime(2026, 8, 2),
                period_text="09:00-10:00",
                message_count=10,
                report=report,
            )
        )
        self.assertEqual(images, ["rendered"])
        html = captured[0]
        for value in (
            "FACT_OVERVIEW",
            "FACT_EVENT",
            "FACT_CLAIM",
            "FACT_OUTCOME",
            "FACT_PENDING",
            "FACT_QUOTE",
            "https://example.test/fact",
        ):
            self.assertIn(value, html)
        self.assertNotIn("ENTERTAINMENT_SHOULD_NOT_RENDER", html)

    @unittest.skipIf(SummaryRenderer is None, "jinja2 is not installed in the test environment")
    def test_user_template_does_not_render_removed_personal_entertainment_fields(self):
        renderer = self._renderer()
        html = renderer._render_template(
            "user_summary_template.html",
            user_name="Alice",
            current_date="2026-08-02",
            avatar_data="",
            message_count=3,
            total_characters=20,
            emoji_count=0,
            summary_text="FACT_PROFILE",
            portrait_html=renderer._render_template(
                "user_portrait_module.html",
                portrait={
                    "topics": ["FACT_TOPIC"],
                    "activity_pattern": "FACT_ACTIVITY",
                    "communication_style": "FACT_STYLE",
                    "evidence_points": [],
                    "title": "REMOVED_TITLE",
                    "mbti": "REMOVED_MBTI",
                    "depression": "REMOVED_DEPRESSION",
                    "ranking": "REMOVED_RANKING",
                    "fun_quote": "REMOVED_FUN_QUOTE",
                },
            ),
        )
        self.assertIn("FACT_PROFILE", html)
        self.assertIn("FACT_TOPIC", html)
        for value in ("REMOVED_TITLE", "REMOVED_MBTI", "REMOVED_DEPRESSION", "REMOVED_RANKING", "REMOVED_FUN_QUOTE"):
            self.assertNotIn(value, html)

    def test_removed_scrapbook_templates_are_not_used(self):
        template_dir = Path(__file__).resolve().parents[1] / "templates" / "scrapbook"
        names = {path.name for path in template_dir.iterdir() if path.is_file()}
        for removed in {
            "depression_index_item.html",
            "quote_item.html",
            "user_title_item.html",
            "user_depression_module.html",
            "activity_chart_section.html",
        }:
            self.assertNotIn(removed, names)


if __name__ == "__main__":
    unittest.main()
