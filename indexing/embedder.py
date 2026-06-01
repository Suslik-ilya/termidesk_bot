from sentence_transformers import SentenceTransformer
from loguru import logger

class LocalEmbedder:
    _instance = None
    _model = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(LocalEmbedder, cls).__new__(cls)
        return cls._instance

    def __init__(self, model_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"):
        if self._model is None:
            logger.info(f"Загрузка локальной модели эмбеддингов: {model_name}")
            self.model = SentenceTransformer(model_name)
            self.__class__._model = self.model
        else:
            self.model = self._model

    def embed_text(self, text: str) -> list[float]:
        vector = self.model.encode(text)
        return vector.tolist()
        
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        vectors = self.model.encode(texts)
        return [v.tolist() for v in vectors]
