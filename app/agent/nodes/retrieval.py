import asyncio
from loguru import logger
from app.agent.state import BotState
from app.services.qdrant_client import QdrantDocClient
from app.services.whoosh_client import WhooshDocClient
from indexing.embedder import LocalEmbedder


# --- Инициализация Singleton-объектов для кэширования ресурсоемких клиентов ---
_embedder = None
_qdrant = None
_whoosh = None


def _get_embedder() -> LocalEmbedder:
    global _embedder
    if _embedder is None:
        _embedder = LocalEmbedder()
    return _embedder


def _get_qdrant() -> QdrantDocClient:
    global _qdrant
    if _qdrant is None:
        _qdrant = QdrantDocClient()
    return _qdrant


def _get_whoosh() -> WhooshDocClient:
    global _whoosh
    if _whoosh is None:
        _whoosh = WhooshDocClient()
    return _whoosh


def _rrf_merge(qdrant_results: list[dict], whoosh_results: list[dict], top_k: int = 5) -> list[dict]:
    """
    Reciprocal Rank Fusion (RRF) — слияние двух ранжированных списков.
    Формула: score(doc) = Σ 1/(60 + rank)
    Возвращает top_k уникальных чанков, отсортированных по RRF-скору.
    """
    scores = {}       # Словарь для хранения RRF-оценок
    doc_data = {}     # Словарь для хранения метаданных чанков

    for rank, doc in enumerate(qdrant_results):
        # Дедупликация фрагментов по контрольной сумме (первые 150 символов)
        key = doc["text"][:150].strip()
        scores[key] = scores.get(key, 0) + 1.0 / (60 + rank)
        if key not in doc_data:
            doc_data[key] = doc

    for rank, doc in enumerate(whoosh_results):
        key = doc["text"][:150].strip()
        scores[key] = scores.get(key, 0) + 1.0 / (60 + rank)
        if key not in doc_data:
            doc_data[key] = doc

    # Сортировка по убыванию RRF-оценки с ограничением выборки
    sorted_keys = sorted(scores.keys(), key=lambda k: scores[k], reverse=True)[:top_k]

    merged = []
    for key in sorted_keys:
        entry = doc_data[key].copy()
        entry["rrf_score"] = round(scores[key], 5)
        merged.append(entry)

    return merged


async def retrieval_node(state: BotState) -> dict:
    """
    Узел 5: Гибридный поиск (Qdrant + Whoosh) с RRF-слиянием.
    """
    logger.info(f"[Узел 5] Гибридный поиск RRF для сессии {state['session_id']}")

    query = state.get("semantic_query") or state.get("rewritten_query") or state.get("original_query", "")
    keywords = state.get("keywords", [])
    version = state.get("target_version")

    if not query:
        logger.warning("[Retrieval] Пустой запрос — пропускаем поиск")
        logger.bind(state=state).debug("[Retrieval] Состояние перед переходом")
        return {"retrieved_chunks": []}

    logger.info(f"[Retrieval] Запрос: '{query}' | Версия: {version}")

    # --- 1. Векторизация запроса (CPU-bound операция в отдельном потоке) ---
    embedder = _get_embedder()
    query_vector = await asyncio.to_thread(embedder.embed_text, query)

    # --- 2. Параллельный поиск: Qdrant (семантика) + Whoosh (BM25) ---
    qdrant_client = _get_qdrant()
    whoosh_client = _get_whoosh()

    # Использование извлеченных ключевых слов для BM25-поиска или fallback на полный запрос
    whoosh_query = " ".join(keywords) if keywords else query

    qdrant_results, whoosh_results = await asyncio.gather(
        asyncio.to_thread(qdrant_client.search, query_vector, version, 20),
        asyncio.to_thread(whoosh_client.search, whoosh_query, version, 20),
    )

    logger.info(
        f"[Retrieval] Qdrant: {len(qdrant_results)} результатов, "
        f"Whoosh: {len(whoosh_results)} результатов"
    )

    # Структурированное логирование для ELK
    logger.bind(
        session_id=state["session_id"],
        qdrant_count=len(qdrant_results),
        whoosh_count=len(whoosh_results),
    ).info("[Retrieval] Гибридный поиск завершён")

    # --- 3. RRF-слияние и Топ-10 ---
    top_chunks = _rrf_merge(qdrant_results, whoosh_results, top_k=10)

    # Журналирование превью найденных фрагментов
    for i, chunk in enumerate(top_chunks):
        preview = chunk["text"][:80].replace("\n", " ")
        logger.debug(f"  [RRF #{i+1}] score={chunk.get('rrf_score', '?')} | {preview}...")

    if not top_chunks:
        logger.warning("[Retrieval] Не найдено ни одного релевантного фрагмента!")

    logger.bind(state=state).debug("[Retrieval] Состояние перед переходом")
    return {"retrieved_chunks": top_chunks}
