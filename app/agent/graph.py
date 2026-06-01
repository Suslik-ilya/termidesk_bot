from langgraph.graph import StateGraph, END
from app.agent.state import BotState
from app.agent.nodes.router import router_node
from app.agent.nodes.slot_filling import slot_filling_node
from app.agent.nodes.cache_check import cache_check_node
from app.agent.nodes.rewriter import rewriter_node
from app.agent.nodes.retrieval import retrieval_node
from app.agent.nodes.evaluator import evaluator_node
from app.agent.nodes.generator import generator_node
from app.agent.nodes.escalation import escalation_node, propose_escalation_node

# --- Маршрутизация условных переходов (Conditional Edges) ---

def route_after_router(state: BotState) -> str:
    """Маршрутизация после Узла 1."""
    intent = state.get("current_intent")
    if intent in ["topic_change_confirmation", "greeting", "off_topic", "escalation_proposal"]:
        return "topic_change_node"  # Транзитный узел: формирование ответа завершено на этапе маршрутизации
    if intent == "escalation":
        return "escalation_node"    # Подтвержденная эскалация: инициация создания тикета
    if state.get("is_version_ambiguous"):
        return "slot_filling_node"  # Активация узла уточнения версии продукта
    return "cache_check_node"       # Перенаправление в кэш по умолчанию

def route_after_cache(state: BotState) -> str:
    """Проверка попадания в кэш (Узел 3)."""
    if state.get("is_from_cache"):
        return "generator_node" # Возврат валидированного кэшированного ответа
    return "rewriter_node"      # Отсутствие в кэше: переход к генерации семантического запроса

def route_after_evaluator(state: BotState) -> str:
    """Логика CRAG-оценки (Узел 6).
    
    - "yes" / "partial" → Generator (генерация на основе доступных фрагментов)
    - "no" → Немедленное предложение эскалации (оптимизация циклов)
    """
    verdict = state.get("evaluator_verdict", "no")
    if verdict == "yes" or verdict == "partial":
        return "generator_node"
    return "propose_escalation_node" # Немедленная эскалация без повторных попыток переформулирования запроса

# --- Сборка Графа ---

workflow = StateGraph(BotState)

# Регистрация узлов графа
workflow.add_node("router_node", router_node)
workflow.add_node("slot_filling_node", slot_filling_node)
workflow.add_node("cache_check_node", cache_check_node)
workflow.add_node("rewriter_node", rewriter_node)
workflow.add_node("retrieval_node", retrieval_node)
workflow.add_node("evaluator_node", evaluator_node)
workflow.add_node("generator_node", generator_node)
workflow.add_node("escalation_node", escalation_node)
workflow.add_node("propose_escalation_node", propose_escalation_node)

# Определение стартового узла
workflow.set_entry_point("router_node")

# Определение конечных состояний (END edges)
workflow.add_edge("slot_filling_node", END)
workflow.add_edge("escalation_node", END)
workflow.add_edge("generator_node", END)
workflow.add_edge("propose_escalation_node", END)

# Транзитный узел для завершения графа после обработки в Роутере
def topic_change_node(state: BotState) -> dict:
    return {}  # Сохранение текущего состояния без мутаций
workflow.add_node("topic_change_node", topic_change_node)
workflow.add_edge("topic_change_node", END)

# Edge после Роутера
workflow.add_conditional_edges(
    "router_node",
    route_after_router,
    {
        "escalation_node": "escalation_node",
        "slot_filling_node": "slot_filling_node",
        "cache_check_node": "cache_check_node",
        "topic_change_node": "topic_change_node"
    }
)

# Edge после Кэша
workflow.add_conditional_edges(
    "cache_check_node",
    route_after_cache,
    {
        "generator_node": "generator_node",
        "rewriter_node": "rewriter_node"
    }
)

# Линейный RAG-конвейер (Препроцессинг -> Поиск -> Оценка)
workflow.add_edge("rewriter_node", "retrieval_node")
workflow.add_edge("retrieval_node", "evaluator_node")

# Маршрутизация после CRAG-оценки
workflow.add_conditional_edges(
    "evaluator_node",
    route_after_evaluator,
    {
        "generator_node": "generator_node",
        "propose_escalation_node": "propose_escalation_node"
    }
)

# Компиляция конечного автомата (StateGraph)
bot_graph = workflow.compile()
