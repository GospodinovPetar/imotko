"""Пуска примерните въпроси от agent_cases.json през агента и проверява поведението му.

Пускане: uv run python evals/run_agent_evals.py
Прави истински заявки към модела и към imot.bg (около минута на въпрос). Безплатните модели
понякога връщат 429 (или дневният им лимит свършва); тогава въпросът се отчита като "моделът не отговори",
а не като лош отговор. Код 1 при лош отговор или ако моделът не е отговорил на нито един въпрос.

Проверява:
- извикани ли са очакваните инструменти;
- споменато ли е поне едно от очакваните неща (например точността на мярката);
- времето с кола в отговора в очаквания интервал ли е;
- има ли в отговора числа, които не идват от инструмент.
"""

import json
import os
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agno.run.base import RunStatus  # noqa: E402

from imotko import tracing  # noqa: E402
from imotko.agent import imot_agent  # noqa: E402
from imotko.quality import monitoring  # noqa: E402

CASES_FILE = Path(__file__).with_name("agent_cases.json")


def check_case(case: dict) -> tuple[bool | None, list[str]]:
    """(True/False, проблеми), или (None, причина), ако моделът изобщо не е отговорил."""
    run = imot_agent.run(case["question"], user_id="evals", session_id=f"eval-{uuid.uuid4().hex[:8]}")
    answer = run.content if isinstance(run.content, str) else ""
    if run.status == RunStatus.error or not answer:
        return None, [f"моделът не отговори: {answer[:120] or 'празен отговор'}"]

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
    if not os.getenv("OPENROUTER_API_KEY"):
        print("✗ OPENROUTER_API_KEY липсва (в .env или като secret в GitHub Actions): въпросите не са пуснати.")
        return 1
    tracing.setup("evals")
    cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))["cases"]
    results = []
    for case in cases:
        ok, problems = check_case(case)
        results.append(ok)
        print(f"{'?' if ok is None else '✓' if ok else '✗'} {case['question']}")
        for problem in problems:
            print(f" - {problem}")

    answered = [ok for ok in results if ok is not None]
    print(f"\n{sum(answered)} от {len(answered)} отговорени минават.")
    if len(answered) < len(cases):
        print(f"⚠ {len(cases) - len(answered)} от {len(cases)} въпроса без отговор от модела (напр. 429 или дневен лимит).")
    return 0 if answered and all(answered) else 1


if __name__ == "__main__":
    sys.exit(main())
