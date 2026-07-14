import uuid
from datetime import datetime, timezone
from loguru import logger

from app.ai.rag.embedder import get_embedder
from app.ai.vector_db.chroma_client import chroma_client

OWNER_USER_ID = -1  # Egani oddiy foydalanuvchilardan ajratish uchun
STYLE_DOC_TYPE = "owner_style"
MAX_STYLE_EXAMPLES = 5  # get_style_context() nechta namuna qaytarishi
MAX_STORED_STYLE_EXAMPLES = 50  # jami saqlanadigan namunalar chegarasi


class StyleLearner:
    """Eganing yozish uslubini o'rganadi va ChromaDB'da saqlaydi."""

    def __init__(self) -> None:
        self._embedder = get_embedder()

    async def learn(self, owner_message: str) -> None:
        """Eganing xabarini uslub namunasi sifatida saqlaydi.

        Aynan bir xil matn allaqachon saqlangan bo'lsa o'tkazib yuboriladi
        (dedup), va jami namunalar soni `MAX_STORED_STYLE_EXAMPLES`dan
        oshsa, eng eskisi o'chiriladi (roadmap Faza 3, band 7).
        """
        text = owner_message.strip()
        if not text or len(text) < 3:
            return

        try:
            existing = await chroma_client.get(
                where={"type": STYLE_DOC_TYPE},
                include=["documents", "metadatas"],
            )
            existing_ids: list[str] = existing.get("ids", [])
            existing_docs: list[str] = existing.get("documents", [])
            existing_metas: list[dict] = existing.get("metadatas", [])

            normalized_new = text.lower()
            if any(doc.strip().lower() == normalized_new for doc in existing_docs):
                logger.debug(f"Uslub namunasi allaqachon mavjud, o'tkazib yuborildi: {text[:60]!r}")
                return

            doc_id = str(uuid.uuid4())
            saved_at = datetime.now(timezone.utc).isoformat()
            embedding = self._embedder.embed_one(text)
            await chroma_client.upsert(
                ids=[doc_id],
                embeddings=[embedding],
                documents=[text],
                metadatas=[{
                    "user_id": OWNER_USER_ID,
                    "type": STYLE_DOC_TYPE,
                    "saved_at": saved_at,
                }],
            )
            logger.debug(f"Uslub namunasi saqlandi: {text[:60]!r}")

            all_entries = list(zip(existing_ids, [m.get("saved_at", "") for m in existing_metas]))
            all_entries.append((doc_id, saved_at))
            overflow = len(all_entries) - MAX_STORED_STYLE_EXAMPLES
            if overflow > 0:
                oldest_ids = [i for i, _ in sorted(all_entries, key=lambda pair: pair[1])[:overflow]]
                await chroma_client.delete(ids=oldest_ids)
                logger.debug(f"{len(oldest_ids)} ta eski uslub namunasi o'chirildi (chegara: {MAX_STORED_STYLE_EXAMPLES})")
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
