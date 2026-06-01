from app.agent.state import BotState
from app.services.llm_client import LLMClient
from app.services.cache_service import CacheService
from loguru import logger

GENERATOR_SCHEMA = {
  "type": "object",
  "properties": {
    "confidence": { "type": "integer", "minimum": 1, "maximum": 10, "description": "Уверенность в полноте ответа" },
    "final_answer": { "type": "string", "description": "Готовый текст ответа в формате Markdown" }
  },
  "required": ["confidence", "final_answer"]
}

GENERATOR_PROMPT = """Ты — инженер технической поддержки Termidesk VDI. Твоя задача — сформировать финальный ответ пользователю на основе предоставленных проверенных фрагментов.

Tone of Voice (Стиль общения):
- Профессиональный, вежливый, с уместной долей эмпатии.
- Обращайся на "Вы".

Строгие правила:
1. Опирайся ТОЛЬКО на предоставленные фрагменты. Если информации для полного ответа нет, честно скажи об этом. ЗАПРЕЩЕНО выдумывать команды или пути к файлам.
2. Форматирование: Используй Markdown. Команды консоли, логи и пути к файлам оформляй в блоки кода (`или ```).
3. Структура: Сначала прямой ответ -> затем пошаговая инструкция (если применимо) -> важные примечания из фрагментов."""

async def generator_node(state: BotState) -> dict:
    logger.info(f"[Узел 7] Симуляция генерации ответа для сессии {state['session_id']}")
    llm = LLMClient()
    
    # Прерывание генерации при наличии ответа из кэша
    if state.get("is_from_cache") and state.get("final_answer"):
        logger.bind(state=state).debug("[Generator] Состояние перед переходом")
        return {} # Возвращаем текущий State (генерация не нужна)

    chunks_text = "\n\n---\n\n".join([c.get('text', '') for c in state.get('retrieved_chunks', [])])
    query = state.get("original_query", "")
    target_version = state.get("target_version")
    context = f"Вопрос пользователя: {query}\n\nФрагменты:\n{chunks_text}"
    
    result = await llm.call_generator(GENERATOR_PROMPT, context, GENERATOR_SCHEMA)

    updates = {
        "final_answer": result["final_answer"],
        "confidence": result["confidence"]
    }

    # Сохранение кандидата перенесено на этап явной положительной оценки (feedback_handler)

    # Структурированное логирование для ELK
    logger.bind(
        session_id=state["session_id"],
        confidence=result["confidence"],
    ).info("[Generator] Ответ сгенерирован")
    
    logger.bind(state=state).debug("[Generator] Состояние перед переходом")
    return updates
