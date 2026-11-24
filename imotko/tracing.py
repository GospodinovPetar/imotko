"""Tracing на агента в Langfuse: всеки отговор, извикване на модела и инструмент, с токените и времето.

Включва се само ако в .env има LANGFUSE_PUBLIC_KEY и LANGFUSE_SECRET_KEY (и LANGFUSE_BASE_URL за регион,
различен от EU). Без тях setup() не прави нищо. Пуска се от входните точки (app.py, chat.py, evals),
не при import, за да не пращат traces тестовете.
"""

import os

from langfuse import Langfuse, get_client
from openinference.instrumentation.agno import AgnoInstrumentor

from imotko.config import VERSION

_enabled = False


def setup(environment: str) -> bool:
    """environment разделя traces в Langfuse (production, development, evals). LANGFUSE_TRACING_ENVIRONMENT има предимство."""
    if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
        return False
    # клиентът регистрира глобалния OpenTelemetry provider, затова е преди instrument(); праща и при изход (atexit)
    Langfuse(release=VERSION, environment=os.getenv("LANGFUSE_TRACING_ENVIRONMENT", environment))
    AgnoInstrumentor().instrument()
    global _enabled
    _enabled = True
    return True


def score_answer(name: str, passed: bool, comment: str | None = None) -> None:
    """Оценка да/не за текущия отговор (trace), за филтриране и графики в Langfuse. Без tracing не прави нищо."""
    if _enabled:
        get_client().score_current_trace(name=name, value=float(passed), data_type="BOOLEAN", comment=comment)
