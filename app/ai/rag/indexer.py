import uuid
from loguru import logger
from app.ai.rag.embedder import get_embedder
from app.ai.vector_db.chroma_client import chroma_client
from app.database.models import MemoryEntry, Message


class RAGIndexer:
    """Xotira va xabarlarni vektor bazasiga indekslaydi."""

    def __init__(self) -> None:
        self._embedder = get_embedder()

    async def index_memory(self, entry: MemoryEntry, user_id: int) -> str:
        """Xotira yozuvini vektorlaydi va saqlaydi."""
        doc_id = entry.uuid or str(uuid.uuid4())
        text = f"{entry.category}: {entry.key} = {entry.value}"
        embedding = self._embedder.embed_one(text)

        await chroma_client.upsert(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[text],
            metadatas=[{
                "user_id": user_id,
                "category": entry.category,
                "key": entry.key,
                "type": "memory",
            }],
        )
        logger.debug(f"Memory indekslandi: {doc_id}")
        return doc_id

    async def index_summary(
        self, summary_id: str, summary_text: str, user_id: int
    ) -> str:
        """Suhbat xulosasini indekslaydi."""
        embedding = self._embedder.embed_one(summary_text)
        await chroma_client.upsert(
            ids=[summary_id],
            embeddings=[embedding],
            documents=[summary_text],
            metadatas=[{"user_id": user_id, "type": "summary"}],
        )
        return summary_id

    async def index_message(self, message: Message, user_id: int) -> str:
        """Muhim xabarlarni indekslaydi."""
        doc_id = message.uuid or str(uuid.uuid4())
        embedding = self._embedder.embed_one(message.content)
        await chroma_client.upsert(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[message.content],
            metadatas=[{
                "user_id": user_id,
                "role": message.role.value,
                "type": "message",
            }],
        )
        return doc_id

    async def index_faq(self, faq_id: int, question: str, answer: str) -> str:
        """FAQ savolini global miqyosda (type=faq) vektorlaydi.

        Foydalanuvchi savoli aynan SAVOL matniga qarab moslanadi, shuning uchun
        embedding savoldan olinadi; javob metadatada saqlanadi (hot-path'da DB
        so'rovisiz javob berish uchun).
        """
        doc_id = f"faq_{faq_id}"
        embedding = self._embedder.embed_one(question)
        await chroma_client.upsert(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[question],
            metadatas=[{
                "type": "faq",
                "faq_id": faq_id,
                "answer": answer,
            }],
        )
        logger.debug(f"FAQ indekslandi: {doc_id}")
        return doc_id

    async def remove_faq(self, vector_id: str) -> None:
        try:
            await chroma_client.delete(ids=[vector_id])
            logger.debug(f"FAQ vektori o'chirildi: {vector_id}")
        except Exception as e:
            logger.warning(f"FAQ vektorini o'chirishda xato ({vector_id}): {e}")

    async def remove_user_data(self, user_id: int) -> None:
        await chroma_client.delete_by_user(user_id)
        logger.info(f"Foydalanuvchi {user_id} vektori o'chirildi")


rag_indexer = RAGIndexer()
