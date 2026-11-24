"""Тестове за оценките в evals/run_experiment.py. Без мрежа и без модел."""

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agno.run.base import RunStatus

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "evals"))
import run_experiment as experiment  # noqa: E402

QUESTION = "Колко е от ул. Елин Връх до УНСС с кола?"
METADATA = {"expected_tools": ["distance_between"], "must_mention_any": ["мин"], "minutes_range": [14, 26]}


class Evaluators(unittest.TestCase):
    def run_task(self, status, content, tools=()):
        run = SimpleNamespace(status=status, content=content, tools=[SimpleNamespace(tool_name=name) for name in tools])
        with mock.patch.object(experiment.imot_agent, "arun", mock.AsyncMock(return_value=run)):
            return asyncio.run(experiment.ask_agent(item=SimpleNamespace(input=QUESTION)))

    def test_good_answer_passes_every_check(self):
        answer = self.run_task(RunStatus.completed, "С кола около 18 мин, 6 км.", ["distance_between"])
        checks = [experiment.tools_called, experiment.mentions_expected, experiment.minutes_in_range]
        results = [check(input=QUESTION, output=answer, metadata=METADATA) for check in checks]
        self.assertTrue(all(result.value for result in results))

    def test_wrong_answer_fails(self):
        answer = self.run_task(RunStatus.completed, "Около 45 мин.", [])
        self.assertFalse(experiment.tools_called(input=QUESTION, output=answer, metadata=METADATA).value)
        self.assertFalse(experiment.minutes_in_range(input=QUESTION, output=answer, metadata=METADATA).value)

    def test_model_error_is_a_failed_run_not_a_bad_answer(self):
        with self.assertRaises(RuntimeError):
            self.run_task(RunStatus.error, "Rate limit exceeded")

    def test_checks_without_metadata_are_skipped(self):
        self.assertEqual(experiment.minutes_in_range(input="въпрос", output="отговор", metadata={}), [])
        self.assertEqual(experiment.no_made_up_numbers(input="въпрос", output="отговор", metadata={}), [])
        made_up = experiment.no_made_up_numbers(input="въпрос", output="около 12 мин", metadata={"expect_no_numbers": True})
        self.assertFalse(made_up.value)


if __name__ == "__main__":
    unittest.main()
