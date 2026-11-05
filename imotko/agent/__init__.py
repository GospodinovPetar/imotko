"""Агентът "Имотко": моделът, инструментите, инструкциите и паметта на разговора."""

from agno.agent import Agent
from agno.db.sqlite import SqliteDb

from imotko import vision
from imotko.agent import hooks, instructions, tools
from imotko.config import DATA_DIR, FALLBACK_MODEL, MAIN_MODEL
from imotko.models import main_and_fallback_models
from imotko.quality import monitoring

CHAT_HISTORY_DB = DATA_DIR / "imot.db"

TOOLS = [
    tools.search_apartments,
    tools.compare_districts,
    tools.distance_between,
    tools.report_error,
    vision.analyze_photos,
    tools.list_saved_searches,
    tools.delete_saved_search,
    tools.check_saved_searches,
]


# При 2048 отговорът понякога оставаше празен след голямо търсене.
MAX_ANSWER_TOKENS = 8192
MAX_TOOL_RESULTS_FROM_HISTORY = 2

main_model, fallback_models = main_and_fallback_models(MAIN_MODEL, FALLBACK_MODEL, max_tokens=MAX_ANSWER_TOKENS, temperature=0.3)

imot_agent = Agent(
    id="imot",
    name="Имотко",
    model=main_model,
    fallback_models=fallback_models,
    instructions=instructions.INSTRUCTIONS,
    db=SqliteDb(db_file=str(CHAT_HISTORY_DB)),
    add_history_to_context=True,
    num_history_runs=3,
    max_tool_calls_from_history=MAX_TOOL_RESULTS_FROM_HISTORY,
    tools=TOOLS,
    tool_hooks=[hooks.return_errors_as_text],
    pre_hooks=[hooks.shrink_uploaded_images],
    post_hooks=[monitoring.check_answer],
)
