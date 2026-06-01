import uuid
import json
import asyncio
from loguru import logger
from qdrant_client import QdrantClient
from qdrant_client.http import models
from config.settings import settings
from app.services.redis_client import RedisStateClient
from indexing.embedder import LocalEmbedder

class CacheService:
    """
    Сервис управления динамическим кэшем на базе Redis (хранение) и Qdrant (семантический поиск).
    """
    def __init__(self):
        self.qdrant = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
        self.collection_name = "cache_collection"
        self.redis = RedisStateClient().client
        self.candidates_key = "cache_candidates_v2"
        self.approved_key = "approved_cache_v2"
        self.embedder = LocalEmbedder()
        self._ensure_collection_exists()

    def _ensure_collection_exists(self):
        try:
            collections = self.qdrant.get_collections().collections
            if not any(c.name == self.collection_name for c in collections):
                logger.info(f"Создание коллекции Qdrant: {self.collection_name}")
                self.qdrant.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=models.VectorParams(
                        size=384,
                        distance=models.Distance.COSINE
                    )
                )
        except Exception as e:
            logger.error(f"Ошибка проверки/создания коллекции Qdrant {self.collection_name}: {e}")

    async def save_candidate(self, query: str, answer: str, target_version: str = None) -> str | None:
        similar = await self.search_cache(query, target_version, top_k=1)
        if similar and similar[0]["similarity_score"] > 0.95:
            logger.info(f"Избежано дублирование кандидата для запроса: {query}")
            return None

        candidate_id = str(uuid.uuid4())
        data = {
            "id": candidate_id,
            "query": query,
            "answer": answer,
            "target_version": target_version,
            "rating": 4.0,  # Изначальный рейтинг 4.0
            "approved_by_admin": False,
            "is_candidate": True
        }
        self.redis.hset(self.candidates_key, candidate_id, json.dumps(data, ensure_ascii=False))
        return candidate_id

    def get_candidates(self) -> list:
        raw = self.redis.hgetall(self.candidates_key)
        return [json.loads(v) for v in raw.values()]

    def get_approved_entries(self) -> list:
        raw = self.redis.hgetall(self.approved_key)
        return [json.loads(v) for v in raw.values()]

    def get_entry(self, entry_id: str):
        raw = self.redis.hget(self.candidates_key, entry_id)
        if raw:
            return json.loads(raw)
        raw = self.redis.hget(self.approved_key, entry_id)
        if raw:
            return json.loads(raw)
        return None

    def update_rating(self, entry_id: str, delta: int):
        entry = self.get_entry(entry_id)
        if not entry:
            return False
            
        current = entry.get("rating", 4.0)
        # Математическое приближение рейтинга к границам 5.0 (положительно) или 2.0 (отрицательно)
        if delta > 0:
            current = current + (5.0 - current) * 0.3
        else:
            current = current - (current - 2.0) * 0.3
            
        entry["rating"] = round(current, 3)
        
        if entry.get("is_candidate"):
            self.redis.hset(self.candidates_key, entry_id, json.dumps(entry, ensure_ascii=False))
        else:
            self.redis.hset(self.approved_key, entry_id, json.dumps(entry, ensure_ascii=False))
            try:
                self.qdrant.set_payload(
                    collection_name=self.collection_name,
                    payload={"rating": entry["rating"]},
                    points=[entry_id]
                )
            except Exception as e:
                logger.error(f"Не удалось обновить рейтинг в Qdrant для {entry_id}: {e}")
        return True

    def edit_answer(self, entry_id: str, new_answer: str):
        entry = self.get_entry(entry_id)
        if not entry:
            return False
            
        entry["answer"] = new_answer
        
        if entry.get("is_candidate"):
            self.redis.hset(self.candidates_key, entry_id, json.dumps(entry, ensure_ascii=False))
        else:
            self.redis.hset(self.approved_key, entry_id, json.dumps(entry, ensure_ascii=False))
            try:
                self.qdrant.set_payload(
                    collection_name=self.collection_name,
                    payload={"answer": entry["answer"]},
                    points=[entry_id]
                )
            except Exception as e:
                logger.error(f"Не удалось обновить ответ в Qdrant для {entry_id}: {e}")
        return True

    async def approve_candidate(self, candidate_id: str):
        raw = self.redis.hget(self.candidates_key, candidate_id)
        if not raw:
            return False
        
        entry = json.loads(raw)
        entry["approved_by_admin"] = True
        entry["is_candidate"] = False
        
        self.redis.hset(self.approved_key, candidate_id, json.dumps(entry, ensure_ascii=False))
        self.redis.hdel(self.candidates_key, candidate_id)
        
        vector = await asyncio.to_thread(self.embedder.embed_text, entry["query"])
        
        payload = {
            "query": entry["query"],
            "answer": entry["answer"],
            "target_version": entry["target_version"],
            "rating": entry["rating"],
            "id": entry["id"]
        }
        
        try:
            self.qdrant.upsert(
                collection_name=self.collection_name,
                points=[
                    models.PointStruct(
                        id=entry["id"],
                        vector=vector,
                        payload=payload
                    )
                ]
            )
        except Exception as e:
            logger.error(f"Не удалось добавить подтвержденный кэш {candidate_id} в Qdrant: {e}")
        return True

    def delete_entry(self, entry_id: str):
        if self.redis.hdel(self.candidates_key, entry_id):
            return True
        if self.redis.hdel(self.approved_key, entry_id):
            try:
                self.qdrant.delete(
                    collection_name=self.collection_name,
                    points_selector=models.PointIdsList(points=[entry_id])
                )
            except Exception as e:
                logger.error(f"Не удалось удалить {entry_id} из Qdrant: {e}")
            return True
        return False

    async def search_cache(self, query: str, target_version: str = None, top_k: int = 3) -> list[dict]:
        vector = await asyncio.to_thread(self.embedder.embed_text, query)
        
        query_filter = None
        if target_version:
            query_filter = models.Filter(
                should=[
                    models.FieldCondition(key="target_version", match=models.MatchValue(value=target_version)),
                    models.IsEmptyCondition(is_empty=models.PayloadField(key="target_version"))
                ]
            )
        
        try:
            response = self.qdrant.query_points(
                collection_name=self.collection_name,
                query=vector,
                query_filter=query_filter,
                limit=top_k
            )
            
            results = []
            for r in response.points:
                score = r.score
                rating = r.payload.get("rating", 4.0)
                # Нормализация рейтинга (2.0-5.0 -> 0.0-1.0)
                norm_rating = (rating - 2.0) / 3.0
                # Формирование итогового балла: 85% семантика, 15% рейтинг
                final_score = (score * 0.85) + (norm_rating * 0.15)
                results.append({
                    "id": str(r.id),
                    "query": r.payload.get("query", ""),
                    "answer": r.payload.get("answer", ""),
                    "target_version": r.payload.get("target_version"),
                    "rating": rating,
                    "similarity_score": score,
                    "final_score": final_score
                })
            
            results.sort(key=lambda x: x["final_score"], reverse=True)
            return results
        except Exception as e:
            logger.error(f"Ошибка поиска в кэше Qdrant: {e}")
            return []
