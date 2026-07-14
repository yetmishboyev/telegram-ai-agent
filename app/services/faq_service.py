"""FAQ / bilim bazasi servisi.

Ega o'rgatgan savol-javoblarni vektor bazada (global, type=faq) saqlaydi va
kiruvchi savolga semantik mos keladiganini topib, agent orqali tabiiy javob
generatsiya qiladi. Mos kelmasa None — bunda xabar odatiy eskalatsiya oqimiga
tushadi.
"""
from loguru import logger

from app.ai.agents.faq_agent import faq_agent
from app.ai.rag.embedder import get_embedder
from app.ai.rag.indexer import rag_indexer
from app.ai.vector_db.chroma_client import chroma_client
from app.database.models import FaqEntry
from app.database.session import AsyncSessionLocal
from app.repositories.faq_repo import (
    create_faq, get_faq, delete_faq, set_vector_id,
)

# Autonom javob berish uchun minimal semantik o'xshashlik (cosine).
# paraphrase-multilingual-MiniLM model haqiqiy parafrazlar uchun ~0.4-0.6 beradi,
# shuning uchun chegara past (recall uchun) — ANIQLIK darvozasi faq_agent:
# mos kelmagan bilimda u NO_ANSWER qaytaradi va xabar eskalatsiyaga tushadi.
FAQ_MATCH_THRESHOLD = 0.40


class FaqService:
    async def search(self, query: str) -> dict | None:
        """Savolga eng mos FAQ ni qaytaradi (o'xshashlik chegaradan yuqori bo'lsa)."""
        try:
            embedding = get_embedder().embed_one(query)
            results = await chroma_client.query(
                query_embeddings=[embedding],
                n_results=3,
                where={"type": "faq"},
            )
        except Exception as e:
            logger.warning(f"FAQ qidiruvda xato: {e}")
            return None

        docs = results.get("documents") or [[]]
        if not docs or not docs[0]:
            return None

        metas = results["metadatas"][0]
        dists = results["distances"][0]
        similarity = 1 - dists[0]  # chroma masofa oshish tartibida — [0] eng yaqin
        if similarity < FAQ_MATCH_THRESHOLD:
            logger.debug(f"FAQ mos kelmadi (o'xshashlik={similarity:.2f})")
            return None

        return {
            "question": docs[0][0],
            "answer": metas[0].get("answer", ""),
            "faq_id": metas[0].get("faq_id"),
            "similarity": round(similarity, 4),
        }

    async def try_answer(self, text: str, lang: str) -> str | None:
        """Bilim bazasidan javob topilsa qaytaradi, aks holda None."""
        match = await self.search(text)
        if not match or not match.get("answer"):
            return None
        answer = await faq_agent.generate(
            user_question=text,
            faq_question=match["question"],
            faq_answer=match["answer"],
            lang=lang,
        )
        if answer:
            logger.info(
                f"FAQ javobi berildi (o'xshashlik={match['similarity']}, faq_id={match['faq_id']})"
            )
        return answer

    async def add_faq(self, question: str, answer: str) -> int:
        """Yangi FAQ ni saqlaydi va vektorlaydi. FAQ id sini qaytaradi."""
        async with AsyncSessionLocal() as db:
            entry = await create_faq(db, question, answer)
            faq_id = entry.id
        try:
            vector_id = await rag_indexer.index_faq(faq_id, question, answer)
            async with AsyncSessionLocal() as db:
                await set_vector_id(db, faq_id, vector_id)
        except Exception as e:
            logger.error(f"FAQ indekslashda xato (faq_id={faq_id}): {e}")
        return faq_id

    async def remove_faq(self, faq_id: int) -> bool:
        """FAQ ni DB va vektor bazadan o'chiradi."""
        async with AsyncSessionLocal() as db:
            entry = await get_faq(db, faq_id)
            if not entry:
                return False
            vector_id = entry.vector_id
            await delete_faq(db, faq_id)
        if vector_id:
            await rag_indexer.remove_faq(vector_id)
        return True


faq_service = FaqService()
