from app.agent.state import BotState
from app.services.llm_client import LLMClient
from loguru import logger

REWRITER_PROMPT = """Ты — помощник, который превращает последний вопрос пользователя в идеальные поисковые запросы для гибридного поиска по базе знаний Termidesk.

Правила:
1. Используй историю диалога, чтобы понять, какой ИМЕННО информации сейчас не хватает. Если пользователь уточняет деталь (например, просто пишет "6.1.1" или "да"), восстанови полный контекст вопроса.
2. `semantic_query` — это осмысленное предложение для поиска по смыслу (Qdrant). Оно должно быть развернутым.
3. `keywords` — это список из 1-3 самых важных терминов, существительных или кодов ошибок для строгого лексического поиска (Whoosh/BM25). Сюда НЕ должны попадать глаголы, предлоги или общие слова вроде "как", "почему", "Termidesk" (если оно везде), "работать". Только суть!

Примеры:
- Вход: "как установить termidesk 6.1"
  Ответ: {"semantic_query": "установка и развертывание termidesk 6.1", "keywords": ["установка", "6.1"]}
- Вход (после вопроса бота "Укажите ОС"): "Astra Linux 1.7"
  Ответ: {"semantic_query": "установка termidesk на операционную систему astra linux 1.7", "keywords": ["Astra Linux", "1.7"]}"""

REWRITER_RETRY_PROMPT = """Ты — помощник, который формирует гибридные поисковые запросы для базы знаний Termidesk.

ВНИМАНИЕ: Предыдущий поисковый запрос не дал хороших результатов. Ты ОБЯЗАН разбить проблему на подтемы и искать недостающую информацию.

Стратегии переформулирования:
- Не повторяй прошлый запрос! Если мы искали проблему целиком, теперь ищи конкретный шаг или конкретную ошибку.
- Используй синонимы.
- Выдели другие `keywords`, которые могли встретиться в документации.

Правила:
1. `semantic_query` — новое, измененное предложение для поиска по смыслу.
2. `keywords` — 1-3 строгих термина для поиска совпадений.

Пример:
- Прошлый запрос: "ошибка 404 при входе через nginx"
- Новый запрос: {"semantic_query": "настройка reverse proxy веб-сервера для доступа к termidesk", "keywords": ["proxy", "web"]}"""

REWRITER_SCHEMA = {
    "type": "object",
    "properties": { 
        "semantic_query": { "type": "string" },
        "keywords": { "type": "array", "items": { "type": "string" } }
    },
    "required": ["semantic_query", "keywords"]
}


async def rewriter_node(state: BotState) -> dict:
    logger.info(f"[Узел 4] Перезапись запроса (Rewriter) для сессии {state['session_id']}")
    llm = LLMClient()
    
    # Извлечение последних 3 сообщений из истории для контекста
    recent_history = state.get("messages", [])[-3:]
    history_str = "\n".join([f"{m['role']}: {m['content']}" for m in recent_history])
    query = state.get("original_query", "")
    
    search_cycles = state.get("search_cycles", 0)
    prev_query = state.get("rewritten_query", "")

    # Выбор промпта в зависимости от номера итерации
    if search_cycles > 0 and prev_query:
        # Повторная итерация: требуется альтернативная формулировка запроса
        prompt = REWRITER_RETRY_PROMPT
        context = (
            f"История:\n{history_str}\n\n"
            f"Текущий вопрос: {query}\n\n"
            f"Попытка поиска №{search_cycles + 1}.\n"
            f"Предыдущий неудачный поисковый запрос: \"{prev_query}\"\n"
            f"Сформулируй запрос ПРИНЦИПИАЛЬНО по-другому!"
        )
        logger.debug(f"[Rewriter Node] Цикл {search_cycles}: переформулируем запрос (пред. запрос: {prev_query})")
    else:
        # Первичная итерация генерации поискового запроса
        prompt = REWRITER_PROMPT
        context = f"История:\n{history_str}\n\nТекущий вопрос: {query}"
    
    result = await llm.structured_call("gpt-4o-mini", prompt, context, REWRITER_SCHEMA, node_name="rewriter")
    logger.debug(f"[Rewriter Node] Результат: {result}")
    
    return {
        "rewritten_query": result["semantic_query"], # сохраняем для логов
        "semantic_query": result["semantic_query"],
        "keywords": result["keywords"]
    }
