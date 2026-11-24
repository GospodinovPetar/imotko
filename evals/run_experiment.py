"""Пуска dataset-а от Langfuse през агента като експеримент и оценява всеки отговор.

Пускане: uv run python evals/run_experiment.py [име на dataset, по подразбиране Imotko]
Нужни са OPENROUTER_API_KEY и ключовете за Langfuse в .env. Всеки въпрос минава през истинския модел и
imot.bg, един по един. Резултатът е dataset run в Langfuse (Datasets -> Imotko -> Runs), където отделните
пускания се сравняват по оценките. Оценките идват от metadata на записа (виж datasets CSV-то):
- tools-called: извикани ли са всички expected_tools;
- mentions-expected: има ли поне едно от must_mention_any и нито едно от must_not_mention;
- minutes-in-range: попада ли времето в minutes_range;
- no-made-up-numbers: няма ли минути, км или цени, когато expect_no_numbers е true.
numbers-verified (числата идват от инструментите) се добавя от monitoring.check_answer за всеки отговор.
"""

import os
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agno.run.base import RunStatus  # noqa: E402
from langfuse import Evaluation, get_client  # noqa: E402

from imotko import tracing  # noqa: E402
from imotko.agent import imot_agent  # noqa: E402
from imotko.config import FALLBACK_MODEL, MAIN_MODEL, VERSION  # noqa: E402

MEASUREMENT = re.compile(r"\d+\s*(?:мин|км|€|евро|лв|£)", re.IGNORECASE)

# извиканите инструменти по въпрос: изходът на задачата е само отговорът, за да се сравнява четимо в Langfuse
called_tools: dict[str, list[str]] = {}


async def ask_agent(*, item, **kwargs) -> str:
    # нов потребител за всеки въпрос, за да не пренася паметта на агента от предишния
    run = await imot_agent.arun(item.input, user_id=f"experiment-{uuid.uuid4().hex[:8]}",
                                session_id=f"experiment-{uuid.uuid4().hex[:8]}")
    if run.status == RunStatus.error:
        # моделът не отговори (напр. 429): грешка на пускането, не лош отговор, затова без оценки
        raise RuntimeError(run.content or "моделът не отговори")
    called_tools[item.input] = [tool.tool_name for tool in (run.tools or [])]
    return run.content if isinstance(run.content, str) else ""


def tools_called(*, input, metadata, **kwargs):
    expected = (metadata or {}).get("expected_tools") or []
    if not expected:
        return []
    tools = called_tools.get(input, [])
    missing = [tool for tool in expected if tool not in tools]
    called = ", ".join(tools) or "нищо"
    return Evaluation(name="tools-called", value=not missing, data_type="BOOLEAN",
                      comment=f"липсва {', '.join(missing)}; извика: {called}" if missing else f"извика: {called}")


def mentions_expected(*, output, metadata, **kwargs):
    metadata = metadata or {}
    answer = output.lower()
    wanted = metadata.get("must_mention_any") or []
    forbidden = [word for word in metadata.get("must_not_mention") or [] if word.lower() in answer]
    if not wanted and not forbidden:
        return []
    found = [word for word in wanted if word.lower() in answer]
    ok = (not wanted or bool(found)) and not forbidden
    comment = f"намерено: {', '.join(found) or 'нищо'}" + (f"; не трябва: {', '.join(forbidden)}" if forbidden else "")
    return Evaluation(name="mentions-expected", value=ok, data_type="BOOLEAN", comment=comment)


def minutes_in_range(*, output, metadata, **kwargs):
    minutes_range = (metadata or {}).get("minutes_range")
    if not minutes_range:
        return []
    low, high = minutes_range
    minutes = [int(value) for value in re.findall(r"(\d+)\s*мин", output)]
    ok = any(low <= value <= high for value in minutes)
    return Evaluation(name="minutes-in-range", value=ok, data_type="BOOLEAN",
                      comment=f"очаквано {low}-{high} мин, в отговора: {minutes or 'няма'}")


def no_made_up_numbers(*, output, metadata, **kwargs):
    if not (metadata or {}).get("expect_no_numbers"):
        return []
    found = MEASUREMENT.findall(output)
    return Evaluation(name="no-made-up-numbers", value=not found, data_type="BOOLEAN",
                      comment=f"намерени: {', '.join(found)}" if found else None)


def pass_rate(*, item_results, **kwargs):
    """Дял на въпросите, при които всички оценки по-горе минават."""
    passed = [all(evaluation.value for evaluation in result.evaluations) for result in item_results]
    rate = sum(passed) / len(passed) if passed else 0.0
    return Evaluation(name="pass-rate", value=rate, comment=f"{sum(passed)} от {len(passed)} отговорени въпроса минават")


def main() -> int:
    dataset_name = sys.argv[1] if len(sys.argv) > 1 else "Imotko"
    if not os.getenv("OPENROUTER_API_KEY"):
        print("✗ OPENROUTER_API_KEY липсва в .env.")
        return 1
    if not tracing.setup("evals"):
        print("✗ Нужни са LANGFUSE_PUBLIC_KEY и LANGFUSE_SECRET_KEY в .env: експериментът се записва в Langfuse.")
        return 1

    dataset = get_client().get_dataset(dataset_name)
    result = dataset.run_experiment(
        name="imotko-agent",
        description=f"Imotko {VERSION}: {MAIN_MODEL} (резервен {FALLBACK_MODEL})",
        task=ask_agent,
        evaluators=[tools_called, mentions_expected, minutes_in_range, no_made_up_numbers],
        run_evaluators=[pass_rate],
        max_concurrency=1,  # безплатните модели връщат 429 при няколко заявки наведнъж
        metadata={"version": VERSION, "model": MAIN_MODEL, "fallback_model": FALLBACK_MODEL},
    )
    print(result.format())
    unanswered = len(dataset.items) - len(result.item_results)
    if unanswered:
        print(f"\n⚠ {unanswered} от {len(dataset.items)} въпроса без отговор от модела (грешка, напр. 429): не са в оценките.")
    get_client().flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
