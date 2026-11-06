"""Фактите от описанието, които влияят на решението: отопление, какво е включено в цената, комисионна, наемател.

Търсим само изрично написаното. Липсата на дума не е доказателство ("не пише" не значи "няма"),
затова при нищо намерено не връщаме нищо.
"""

import re

MAX_INCLUDED_WORDS = 4

HEATING = re.compile(
    r"отоплени\w*\s*[:\-]?\s*(?P<what>газ|тец|парно|климатик\w*|ток|електрич\w+|локално|дърва|пелети|термопомпа)",
    re.IGNORECASE,
)
INCLUDED = re.compile(r"в цената\b(?P<between>[^.!;]{0,40}?)включен\w*\s+(?:и\s+)?(?P<what>[^.!;,]{3,50})", re.IGNORECASE)
NO_COMMISSION = re.compile(r"без комисион\w*", re.IGNORECASE)
TENANT = re.compile(r"(?<!без )\bс наемател\b|\bотдаден\w* под наем\b", re.IGNORECASE)


def find_facts(description: str | None) -> list[str]:
    """Кратки изрази за картата: ["отопление: газ", "в цената: гараж", "без комисионна", "с наемател"]."""
    if not description:
        return []

    facts = []
    heating = HEATING.search(description)
    if heating:
        facts.append(f"отопление: {heating.group('what').lower()}")

    for included in INCLUDED.finditer(description):
        if re.search(r"\bне\b", included.group("between"), re.IGNORECASE):
            continue  # "в цената не е включен ..."
        words = included.group("what").lower().split()[:MAX_INCLUDED_WORDS]
        facts.append(f"в цената: {' '.join(words)}")
        break

    if NO_COMMISSION.search(description):
        facts.append("без комисионна")
    if TENANT.search(description):
        facts.append("с наемател")
    return facts
