import redis
import json
from loguru import logger
from config.settings import settings

class RedisStateClient:
    def __init__(self):
        self.client = redis.Redis(
            host=settings.redis_host, 
            port=settings.redis_port, 
            decode_responses=True # Автоматическое декодирование байтов в строки
        )
        self.state_prefix = "bot_state:"
        self.cache_candidates_key = "cache_candidates"
        self.approved_cache_key = "approved_cache"

    def get_state(self, session_id: str) -> dict:
        """
        Чтение состояния сессии диалога из Redis.
        При отсутствии данных возвращает инициализированный шаблон BotState.
        """
        key = f"{self.state_prefix}{session_id}"
        try:
            data = self.client.get(key)
            if data:
                return json.loads(data)
        except Exception as e:
            logger.error(f"Ошибка чтения BotState для сессии {session_id} из Redis: {e}")
            
        # Возврат дефолтного состояния при отсутствии данных
        return {
            "session_id": session_id,
            "messages": [],
            "current_intent": None,
            "target_version": None,
            "is_version_ambiguous": False,
            "retrieved_chunks": [],
            "search_cycles": 0,
            "needs_escalation": False,
            "semantic_query": None,
            "keywords": [],
            "topic_change_pending_query": None
        }

    def save_state(self, session_id: str, state: dict, expire_seconds: int = 86400):
        """
        Сохранение состояния диалога (BotState) в Redis.
        Время жизни контекста (TTL) по умолчанию: 24 часа.
        """
        key = f"{self.state_prefix}{session_id}"
        try:
            self.client.setex(key, expire_seconds, json.dumps(state, ensure_ascii=False))
        except Exception as e:
            logger.error(f"Ошибка сохранения BotState для сессии {session_id} в Redis: {e}")

    def clear_state(self, session_id: str) -> None:
        """Полностью удаляет стейт сессии из Redis."""
        key = f"{self.state_prefix}{session_id}"
        try:
            self.client.delete(key)
        except Exception as e:
            logger.error(f"Ошибка удаления BotState для сессии {session_id}: {e}")

    # --- Методы кэширования ответов ---

    def save_cache_candidate(self, query: str, answer: str) -> None:
        """Сохраняет пару вопрос-ответ как кандидата в кэш."""
        import uuid
        candidate_id = str(uuid.uuid4())
        candidate_data = {
            "id": candidate_id,
            "query": query,
            "answer": answer
        }
        try:
            self.client.hset(self.cache_candidates_key, candidate_id, json.dumps(candidate_data, ensure_ascii=False))
        except Exception as e:
            logger.error(f"Ошибка сохранения кандидата в кэш: {e}")

    def get_cache_candidates(self) -> list:
        """Возвращает список всех кандидатов."""
        try:
            candidates_raw = self.client.hgetall(self.cache_candidates_key)
            return [json.loads(data) for data in candidates_raw.values()]
        except Exception as e:
            logger.error(f"Ошибка получения кандидатов из кэша: {e}")
            return []

    def approve_cache_candidate(self, candidate_id: str) -> bool:
        """Одобряет кандидата, перенося его в боевой кэш."""
        try:
            candidate_raw = self.client.hget(self.cache_candidates_key, candidate_id)
            if not candidate_raw:
                return False
            
            candidate_data = json.loads(candidate_raw)
            query = candidate_data["query"]
            answer = candidate_data["answer"]
            
            # Сохраняем в боевой кэш (по ключу: сам запрос)
            self.client.hset(self.approved_cache_key, query, answer)
            # Удаляем из кандидатов
            self.client.hdel(self.cache_candidates_key, candidate_id)
            return True
        except Exception as e:
            logger.error(f"Ошибка одобрения кандидата {candidate_id}: {e}")
            return False

    def reject_cache_candidate(self, candidate_id: str) -> bool:
        """Отклоняет и удаляет кандидата."""
        try:
            return bool(self.client.hdel(self.cache_candidates_key, candidate_id))
        except Exception as e:
            logger.error(f"Ошибка отклонения кандидата {candidate_id}: {e}")
            return False

    def get_cached_answer(self, query: str) -> str | None:
        """Ищет точное совпадение вопроса в боевом кэше."""
        try:
            return self.client.hget(self.approved_cache_key, query)
        except Exception as e:
            logger.error(f"Ошибка получения кэшированного ответа: {e}")
            return None
