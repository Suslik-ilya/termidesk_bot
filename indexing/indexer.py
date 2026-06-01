import os
import hashlib
import json
import uuid
from loguru import logger
from indexing.pdf_parser import PDFParser
from indexing.embedder import LocalEmbedder
from app.services.qdrant_client import QdrantDocClient
from app.services.whoosh_client import WhooshDocClient

class Indexer:
    def __init__(self, data_dir: str = "data"):
        self.pdfs_dir = os.path.join(data_dir, "pdfs")
        self.state_file = os.path.join(data_dir, "processed_files.json")
        
        self.parser = PDFParser()
        self.embedder = LocalEmbedder()
        self.qdrant = QdrantDocClient()
        self.whoosh = WhooshDocClient(index_dir=os.path.join(data_dir, "whoosh_index"))

    def _calculate_md5(self, file_path: str) -> str:
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()

    def _load_state(self) -> dict:
        if os.path.exists(self.state_file):
            with open(self.state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save_state(self, state: dict):
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=4)

    def run(self):
        logger.info("Начало инкрементального индексирования...")
        current_state = self._load_state()

        if not os.path.exists(self.pdfs_dir):
            logger.warning(f"Директория {self.pdfs_dir} не существует. Пропуск.")
            return

        processed_files = set()

        # Итерация по директориям версий документации (например, "5.1", "6.1")
        for version_dir in os.listdir(self.pdfs_dir):
            version_path = os.path.join(self.pdfs_dir, version_dir)
            if not os.path.isdir(version_path):
                continue
            
            version = version_dir

            for file_name in os.listdir(version_path):
                if not file_name.endswith(".pdf"):
                    continue
                    
                file_path = os.path.join(version_path, file_name)
                source_file = f"{version}/{file_name}" # Идентификация файла по относительному пути для версионирования
                processed_files.add(source_file)
                
                file_hash = self._calculate_md5(file_path)
                
                # Инкрементальная проверка по MD5-хэшу
                if source_file in current_state and current_state[source_file] == file_hash:
                    logger.debug(f"Пропуск неизменённого файла: {source_file}")
                    continue
                
                logger.info(f"Индексирование файла: {source_file} (Версия: {version})")
                
                # БЕЗУСЛОВНАЯ инвалидация старых записей (защита от дублей при любых сбоях)
                logger.info(f"Очистка возможных старых чанков для файла: {source_file}")
                self.qdrant.delete_by_source_file(source_file)
                self.whoosh.delete_by_source_file(source_file)

                # Извлечение и семантическое разбиение текста с сохранением структуры заголовков
                chunks = self.parser.parse_and_chunk(file_path)
                
                for chunk in chunks:
                    chunk_id = str(uuid.uuid4())
                    text = chunk['text']
                    page = chunk['page']
                    
                    vector = self.embedder.embed_text(text)
                    
                    self.qdrant.upload_chunk(
                        chunk_id=chunk_id, 
                        vector=vector, 
                        text=text, 
                        version=version, 
                        source_file=source_file, 
                        page=page
                    )
                    
                    self.whoosh.add_document(
                        chunk_id=chunk_id,
                        content=text,
                        version=version,
                        source=source_file
                    )
                
                current_state[source_file] = file_hash
                self._save_state(current_state)
        
        # Очистка индексов от удаленных (orphan) файлов
        keys_to_delete = []
        for old_source_file in current_state.keys():
            if old_source_file not in processed_files:
                logger.info(f"Удаление записей для удалённого файла: {old_source_file}")
                self.qdrant.delete_by_source_file(old_source_file)
                self.whoosh.delete_by_source_file(old_source_file)
                keys_to_delete.append(old_source_file)
                
        for k in keys_to_delete:
            del current_state[k]
        self._save_state(current_state)
        logger.info("Индексирование завершено.")

if __name__ == "__main__":
    indexer = Indexer()
    indexer.run()
