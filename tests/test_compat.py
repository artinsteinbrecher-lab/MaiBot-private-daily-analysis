import unittest
from types import SimpleNamespace

from core.compat import inspect_runtime


def _callable():
    return lambda *args, **kwargs: None


class CompatibilityTests(unittest.TestCase):
    def _ctx(self, *, memory=False):
        ctx = SimpleNamespace(
            llm=SimpleNamespace(generate=_callable(), get_available_models=_callable()),
            message=SimpleNamespace(get_by_time_in_chat=_callable()),
            chat=SimpleNamespace(
                get_group_streams=_callable(), open_session=_callable()
            ),
            send=SimpleNamespace(text=_callable(), image=_callable()),
            render=SimpleNamespace(html2png=_callable()),
        )
        if memory:
            ctx.maisaka = SimpleNamespace(
                context=SimpleNamespace(append=_callable())
            )
        return ctx

    def test_current_capabilities_are_accepted(self):
        report = inspect_runtime(self._ctx())
        self.assertTrue(report.ok)
        self.assertEqual(report.missing_optional, ())

    def test_memory_capability_is_optional_when_disabled(self):
        report = inspect_runtime(self._ctx(), inject_memory=False)
        self.assertTrue(report.ok)
        self.assertEqual(report.missing_optional, ())

    def test_missing_required_capability_is_reported(self):
        ctx = self._ctx()
        ctx.render.html2png = None
        report = inspect_runtime(ctx)
        self.assertFalse(report.ok)
        self.assertEqual(report.missing_required, ("render.html2png",))

    def test_enabled_memory_reports_missing_optional_capability(self):
        report = inspect_runtime(self._ctx(), inject_memory=True)
        self.assertTrue(report.ok)
        self.assertEqual(report.missing_optional, ("maisaka.context.append",))


if __name__ == "__main__":
    unittest.main()
