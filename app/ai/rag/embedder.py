from functools import lru_cache
from sentence_transformers import SentenceTransformer
from loguru import logger


class EmbeddingService:
    """Sentence Transformers orqali matnni vektorizatsiya qiladi."""

    MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

    def __init__(self) -> None:
        logger.info(f"Embedding modeli yuklanmoqda: {self.MODEL_NAME}")
        self._model = SentenceTransformer(self.MODEL_NAME)
        logger.info("Embedding modeli tayyor")

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Matnlar ro'yxatini vektorlarga aylantiradi."""
        embeddings = self._model.encode(texts, show_progress_bar=False, normalize_embeddings=True)
        return embeddings.tolist()

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]

    @property
    def dimension(self) -> int:
        return self._model.get_sentence_embedding_dimension()


@lru_cache(maxsize=1)
def get_embedder() -> EmbeddingService:
    return EmbeddingService()
