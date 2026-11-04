"""Пуска примерните въпроси от agent_cases.json през агента и проверява поведението му.

Пускане: uv run python evals/run_agent_evals.py
Прави истински заявки към модела и към imot.bg (около минута на въпрос). Безплатните модели
понякога връщат 429; тогава въпросът се отчита като "моделът не отговори", не като грешка.

Проверява:
- извикани ли са очакваните инструменти;
- споменато ли е поне едно от очакваните неща (например точността на мярката);
- времето с кола в отговора в очаквания интервал ли е;
- има ли в отговора числа, които не идват от инструмент.
"""

import json
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from imotko.agent import imot_agent  # noqa: E402
from imotko.quality import monitoring  # noqa: E402

CASES_FILE = Path(__file__).with_name("agent_cases.json")


def check_case(case: dict) -> tuple[bool, list[str]]:
    run = imot_agent.run(case["question"], user_id="evals", session_id=f"eval-{uuid.uuid4().hex[:8]}")
    answer = run.content if isinstance(run.content, str) else ""
    if not answer:
        return False, ["моделът не отговори"]

    problems = []
    called = [tool.tool_name for tool in (run.tools or [])]
    for tool_name in case.get("expected_tools", []):
        if tool_name not in called:
            problems.append(f"не извика {tool_name} (извика: {', '.join(called) or 'нищо'})")

    expected_words = case.get("must_mention_any", [])
    if expected_words and not any(word.lower() in answer.lower() for word in expected_words):
        problems.append(f"не споменава нито едно от: {', '.join(expected_words)}")

    if "minutes_between" in case:
        low, high = case["minutes_between"]
        minutes = [int(value) for value in re.findall(r"(\d+)\s*мин", answer)]
        if not any(low <= value <= high for value in minutes):
            problems.append(f"времето не е между {low} и {high} мин (в отговора: {minutes})")

    suspicious = monitoring.suspicious_numbers(run)
    if suspicious:
        problems.append(f"числа, които не идват от инструмент: {', '.join(suspicious)}")

    return not problems, problems


def main() -> int:
    cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))["cases"]
    passed = 0
    for case in cases:
        ok, problems = check_case(case)
        passed += ok
        print(f"{'✓' if ok else '✗'} {case['question']}")
        for problem in problems:
            print(f"    - {problem}")

    print(f"\n{passed} от {len(cases)} минават.")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
