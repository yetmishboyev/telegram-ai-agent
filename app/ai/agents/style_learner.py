import uuid
from datetime import datetime, timezone
from loguru import logger

from app.ai.rag.embedder import get_embedder
from app.ai.vector_db.chroma_client import chroma_client

OWNER_USER_ID = -1  # Egani oddiy foydalanuvchilardan ajratish uchun
STYLE_DOC_TYPE = "owner_style"
MAX_STYLE_EXAMPLES = 5


class StyleLearner:
    """Eganing yozish uslubini o'rganadi va ChromaDB'da saqlaydi."""

    def __init__(self) -> None:
        self._embedder = get_embedder()

    async def learn(self, owner_message: str) -> None:
        """Eganing xabarini uslub namunasi sifatida saqlaydi."""
        text = owner_message.strip()
        if not text or len(text) < 3:
            return

        doc_id = str(uuid.uuid4())
        try:
            embedding = self._embedder.embed_one(text)
            await chroma_client.upsert(
                ids=[doc_id],
                embeddings=[embedding],
                documents=[text],
                metadatas=[{
                    "user_id": OWNER_USER_ID,
                    "type": STYLE_DOC_TYPE,
                    "saved_at": datetime.now(timezone.utc).isoformat(),
                }],
            )
            logger.debug(f"Uslub namunasi saqlandi: {text[:60]!r}")
        except Exception as e:
            logger.warning(f"Uslub saqlashda xato: {e}")

    async def get_style_context(self, query: str) -> str:
        """Berilgan so'rovga o'xshash uslub namunalarini qaytaradi."""
        try:
            embedding = self._embedder.embed_one(query)
            results = await chroma_client.query(
                query_embeddings=[embedding],
                n_results=MAX_STYLE_EXAMPLES,
                where={"type": STYLE_DOC_TYPE},
            )
            docs = results.get("documents", [[]])[0]
            if not docs:
                return ""
            examples = [d for d in docs if d.strip()][:3]
            if not examples:
                return ""
            lines = ["## Eganing yozish uslubidan namunalar (o'rganilgan):"]
            for ex in examples:
                lines.append(f"- {ex}")
            return "\n".join(lines)
        except Exception as e:
            logger.debug(f"Uslub konteksti olishda xato: {e}")
            return ""


style_learner = StyleLearner()
