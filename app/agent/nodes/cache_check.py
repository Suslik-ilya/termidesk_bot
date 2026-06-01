from app.agent.state import BotState
from app.services.cache_service import CacheService
from loguru import logger
from app.services.metrics import bot_cache_hits_total

async def cache_check_node(state: BotState) -> dict:
    logger.info(f"[Узел 3] Проверка кэша для сессии {state['session_id']}")
    
    query = state.get("original_query", "")
    target_version = state.get("target_version")
    intent = state.get("current_intent")
    
    # Исключение из кэширования пустых запросов, приветствий и эскалаций
    if not query or intent in ["greeting", "escalation"]:
        return {"is_from_cache": False}
        
    cache_service = CacheService()
    results = await cache_service.search_cache(query, target_version, top_k=3)
    
    rejected_ids = state.get("rejected_cache_ids", [])
    
    for hit in results:
        # Проверка статуса отклонения записи пользователем в текущей сессии
        if hit["id"] in rejected_ids:
            continue
            
        # Проверка строгого порога соответствия (cosine similarity + rating)
        if hit["similarity_score"] > 0.95 and hit["final_score"] > 0.95:
            logger.info(f"Найден ответ в кэше для запроса: {query} (ID: {hit['id']})")
            bot_cache_hits_total.inc()
            return {
                "is_from_cache": True,
                "final_answer": hit["answer"],
                "last_served_cache_id": hit["id"],
                "confidence": 10
            }
            
    return {"is_from_cache": False}
