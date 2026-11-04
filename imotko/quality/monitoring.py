"""Следене на отговорите: всяко число в отговора трябва да идва от инструмент.

check_answer се закача към агента като post_hook. Ако моделът е написал число, което не идва от нито
един инструмент (в този или в последните няколко отговора), отговорът се записва в базата
(store.list_feedback("unverified_numbers")) и към него се добавя предупреждение.

Ограничение: в уеб интерфейса отговорът се показва, докато се пише, и предупреждението
може да не се види там. Записът в базата се прави винаги.
"""

import bisect
import re

from imotko import store

# "117 000", "117,000", "5.7", "5,7", "600"
NUMBER = re.compile(r"(?<![\w.])\d{1,3}(?:[  ,]\d{3})+(?![\d])|\d+(?:[.,]\d+)?")
URL = re.compile(r"https?://\S+")

WARNING_MARKER = "\n\n⚠ Проверка:"
SMALL_NUMBER = 10
TOLERANCE = 0.03
HISTORY_RUNS = 3


def numbers_in(text: str) -> list[float]:
    """Числата в текста, без тези в линковете."""
    values = []
    for token in NUMBER.findall(URL.sub(" ", text)):
        values.append(_to_number(token))
    return values


def _to_number(token: str) -> float:
    compact = token.replace(" ", " ")
    if re.fullmatch(r"\d{1,3}(?:[ ,]\d{3})+", compact):
        return float(re.sub(r"[ ,]", "", compact))  # хилядите са разделени с интервал или запетая
    return float(compact.replace(",", "."))


# Думи, до които моделът пише сметнато число ("80 € разлика", "общо 650 €")
CALCULATION_WORDS = re.compile(r"разлик|повече|по-малко|по-евтин|по-скъп|общо|заедно|сбор|\+", re.IGNORECASE)
CONTEXT_CHARS = 30


def unverified_numbers(answer: str, sources: list[str]) -> list[str]:
    """Числата от отговора, които ги няма (дори закръглени) в нито един източник.

    Число до дума за сметка ("разлика", "общо") минава, ако е сбор или разлика на две известни числа.
    """
    known = set()
    for source in sources:
        known.update(numbers_in(source))

    unverified = []
    answer = URL.sub(" ", answer)
    for match in NUMBER.finditer(answer):
        value = _to_number(match.group())
        if value <= SMALL_NUMBER or _is_known(value, known):
            continue

        context_start = max(0, match.start() - CONTEXT_CHARS)
        context_end = match.end() + CONTEXT_CHARS
        context = answer[context_start:context_end]
        if CALCULATION_WORDS.search(context) and _is_sum_or_difference(value, known):
            continue

        text = f"{value:g}"
        if text not in unverified:
            unverified.append(text)
    return unverified


def _is_known(value: float, known: set[float]) -> bool:
    for known_value in known:
        allowed_difference = max(1.0, known_value * TOLERANCE)
        if abs(value - known_value) <= allowed_difference:
            return True
    return False


def _is_sum_or_difference(value: float, known: set[float]) -> bool:
    numbers = sorted(number for number in known if number > SMALL_NUMBER)
    for index, first in enumerate(numbers):
        for needed_second in (value - first, first - value):
            if _has_other_number_near(numbers, needed_second, index):
                return True
    return False


def _has_other_number_near(sorted_numbers: list[float], target: float, skip_index: int) -> bool:
    low = bisect.bisect_left(sorted_numbers, target - 1)
    high = bisect.bisect_right(sorted_numbers, target + 1)
    for position in range(low, high):
        if position != skip_index:
            return True
    return False


def _tool_results(run_output, session) -> list[str]:
    """Резултатите на инструментите от този отговор и от последните няколко в разговора."""
    runs = [run_output]
    if session is not None and getattr(session, "runs", None):
        for previous_run in session.runs[-HISTORY_RUNS:]:
            if previous_run is not run_output:
                runs.append(previous_run)

    results = []
    for run in runs:
        for tool in getattr(run, "tools", None) or []:
            if tool.result:
                results.append(str(tool.result))
    return results


def _question(run_output) -> str:
    run_input = getattr(run_output, "input", None)
    if not run_input:
        return ""

    content = getattr(run_input, "input_content", "")
    if isinstance(content, str):
        return content
    return str(content)


def suspicious_numbers(run_output, session=None) -> list[str]:
    """Числата в отговора (без добавеното предупреждение), които не идват от инструмент или от въпроса."""
    content = run_output.content
    if not isinstance(content, str):
        return []

    answer_without_warning = content.split(WARNING_MARKER)[0]
    if not answer_without_warning:
        return []

    sources = _tool_results(run_output, session) + [_question(run_output)]
    return unverified_numbers(answer_without_warning, sources)


def check_answer(run_output, session=None) -> None:
    """post_hook за агента. Никога не спира отговора, дори ако самата проверка се счупи."""
    try:
        suspicious = suspicious_numbers(run_output, session)
        if not suspicious:
            return

        tool_names = [tool.tool_name for tool in (run_output.tools or [])]
        question = _question(run_output)
        answer = run_output.content
        store.add_feedback(
            "unverified_numbers",
            question=question[:2000],
            answer=answer[:4000],
            data={"numbers": suspicious, "tools": tool_names},
        )
        run_output.content = answer + (
            f"{WARNING_MARKER} числата {', '.join(suspicious)} не идват директно от инструментите "
            "(може да са сметнати или казани по памет). Провери ги."
        )
    except Exception:
        pass
