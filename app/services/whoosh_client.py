import os
from whoosh.index import create_in, open_dir, exists_in
from whoosh.fields import Schema, TEXT, ID, STORED
from whoosh.qparser import QueryParser, OrGroup
from loguru import logger

class WhooshDocClient:
    def __init__(self, index_dir: str = "data/whoosh_index"):
        self.index_dir = index_dir
        
        # Убедимся, что директория существует
        os.makedirs(self.index_dir, exist_ok=True)
        
        self.schema = Schema(
            id=ID(stored=True, unique=True),
            content=TEXT(stored=True),  # Параметр stored=True необходим для извлечения исходного текста при поиске
            version=ID(stored=True),
            source=STORED()
        )
        
        if not exists_in(self.index_dir):
            logger.info(f"Создание индекса Whoosh в {self.index_dir}")
            self.index = create_in(self.index_dir, self.schema)
        else:
            logger.debug(f"Открытие существующего индекса Whoosh в {self.index_dir}")
            self.index = open_dir(self.index_dir)

    def add_document(self, chunk_id: str, content: str, version: str, source: str):
        writer = self.index.writer()
        writer.update_document(
            id=chunk_id,
            content=content,
            version=version,
            source=source
        )
        writer.commit()

    def search(self, query_text: str, version: str = None, limit: int = 10) -> list[dict]:
        """
        BM25 полнотекстовый поиск с опциональной фильтрацией по версии.
        Возвращает список найденных чанков.
        """
        try:
            with self.index.searcher() as searcher:
                # Использование OrGroup для поиска фрагментов с частичным совпадением ключевых слов
                parser = QueryParser("content", self.index.schema, group=OrGroup.factory(0.9))
                query = parser.parse(query_text)
                
                # Фильтр по версии
                version_filter = None
                if version:
                    version_parser = QueryParser("version", self.index.schema)
                    version_filter = version_parser.parse(version)
                
                results = searcher.search(query, filter=version_filter, limit=limit)
                
                logger.debug(f"Поиск в Whoosh вернул {len(results)} результатов (версия={version})")
                
                found = []
                for r in results:
                    found.append({
                        "text": r.get("content", ""),
                        "source_file": r.get("source", ""),
                        "id": r.get("id", ""),
                        "score": r.score
                    })
                return found
        except Exception as e:
            logger.error(f"Ошибка поиска в Whoosh: {e}")
            return []

    def delete_by_source_file(self, source_file: str):
        writer = self.index.writer()
        writer.delete_by_term('source', source_file)
        writer.commit()
