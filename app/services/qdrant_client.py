from qdrant_client import QdrantClient
from qdrant_client.http import models
from config.settings import settings
import uuid
from loguru import logger

class QdrantDocClient:
    def __init__(self):
        self.client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
        self.collection_name = "termidesk_docs"
        self._ensure_collection_exists()

    def _ensure_collection_exists(self):
        try:
            collections = self.client.get_collections().collections
            if not any(c.name == self.collection_name for c in collections):
                logger.info(f"Создание коллекции Qdrant: {self.collection_name}")
                self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=models.VectorParams(
                        size=384,  # corresponding to MiniLM-L12-v2
                        distance=models.Distance.COSINE
                    )
                )
            else:
                logger.debug(f"Коллекция Qdrant {self.collection_name} уже существует.")
        except Exception as e:
            logger.error(f"Ошибка проверки/создания коллекции Qdrant: {e}")

    def upload_chunk(self, chunk_id: str, vector: list[float], text: str, version: str, source_file: str, page: int):
        payload = {
            "text": text,
            "version": version,
            "source_file": source_file,
            "page": page
        }
        self.client.upsert(
            collection_name=self.collection_name,
            points=[
                models.PointStruct(
                    id=chunk_id,
                    vector=vector,
                    payload=payload
                )
            ]
        )

    def search(self, query_vector: list[float], version: str = None, limit: int = 10) -> list[dict]:
        """
        Векторный поиск по коллекции termidesk_docs.
        Возвращает список чанков, отсортированных по cosine similarity.
        """
        query_filter = None
        if version:
            query_filter = models.Filter(
                must=[
                    models.FieldCondition(
                        key="version",
                        match=models.MatchValue(value=version)
                    )
                ]
            )

        try:
            response = self.client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                query_filter=query_filter,
                limit=limit
            )
            results = response.points
            logger.debug(f"Поиск в Qdrant вернул {len(results)} результатов (версия={version})")
            return [
                {
                    "text": r.payload.get("text", ""),
                    "source_file": r.payload.get("source_file", ""),
                    "page": r.payload.get("page", 0),
                    "score": r.score
                }
                for r in results
            ]
        except Exception as e:
            logger.error(f"Ошибка поиска в Qdrant: {e}")
            return []

    def delete_by_source_file(self, source_file: str):
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=models.Filter(
                must=[
                    models.FieldCondition(
                        key="source_file",
                        match=models.MatchValue(value=source_file)
                    )
                ]
            )
        )
