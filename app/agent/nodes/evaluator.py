from app.agent.state import BotState
from app.services.llm_client import LLMClient
from loguru import logger
from app.services.metrics import bot_search_cycles

EVALUATOR_SCHEMA = {
  "type": "object",
  "properties": {
    "is_relevant": { "type": "string", "enum": ["yes", "no", "partial"] },
    "reasoning": { "type": "string", "description": "Краткое обоснование вердикта (для логов)" }
  },
  "required": ["is_relevant", "reasoning"]
}

EVALUATOR_PROMPT = """Ты — строгий технический аудитор (Судья). Тебе дан вопрос пользователя и фрагменты (чанки) официальной документации Termidesk.
Твоя задача — определить, содержат ли эти фрагменты достаточно информации для решения проблемы пользователя.

Правило оценки:
- "yes": Фрагменты содержат четкий ответ, алгоритм или команду для решения вопроса.
- "partial": Есть описание причины ошибки или смежная информация, но нет конкретной инструкции по исправлению.
- "no": Информация в фрагментах не относится к вопросу.

Будь критичен. Простое совпадение ключевых слов (например, упоминание кода ошибки без решения) — это "no" или "partial"."""

async def evaluator_node(state: BotState) -> dict:
    logger.info(f"[Узел 6] LLM-Судья (CRAG) анализирует документы для сессии {state['session_id']}")
    llm = LLMClient()
    
    chunks_text = "\n\n---\n\n".join([c['text'] for c in state.get('retrieved_chunks', [])])
    query = state.get("original_query", "")
    context = f"Вопрос пользователя: {query}\n\nНайденные фрагменты:\n{chunks_text}"
    
    result = await llm.call_evaluator(EVALUATOR_PROMPT, context, EVALUATOR_SCHEMA)
    logger.debug(f"[Evaluator Node] Вердикт: {result}")
    
    verdict = result["is_relevant"]
    cycles = state.get("search_cycles", 0)

    # Структурированное логирование для ELK
    logger.bind(
        session_id=state["session_id"],
        verdict=verdict,
        cycles=cycles,
    ).info("[Evaluator] Оценка документов завершена")
    
    # Увеличение счетчика попыток исключительно при полном отсутствии релевантных результатов (статус 'no')
    if verdict == "no":
        cycles += 1
        
    bot_search_cycles.observe(cycles)
        
    # Предложение эскалации после 3 неудачных циклов поиска
    needs_esc = (verdict == "no" and cycles >= 3)
    
    logger.bind(state=state).debug("[Evaluator] Состояние перед переходом")
    return {
        "search_cycles": cycles,
        "needs_escalation": needs_esc,
        "evaluator_verdict": verdict,
        "evaluator_reasoning": result["reasoning"]
    }
