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

# Agentga beriladigan nomzodlar soni.
#
# 5 ga qo'yilgan, chunki embedding modeli (paraphrase-multilingual-MiniLM)
# o'zbekchani yomon tartiblaydi. O'lchangan misol (2026-08-30):
# "Loyihalaringizni ko'rsam bo'ladimi?" savolida to'g'ri FAQ 4-o'rinda turdi —
# undan yuqorida CV (0.712), ish (0.610) va tadbir (0.477) FAQ lari edi.
# 3 ta nomzodda to'g'ri javob umuman ko'rinmasdi.
#
# Bu bilim bazasi KICHIK ekaniga tayangan yechim: 5 nomzod hozir deyarli butun
# bazani qamrab oladi va tanlashni agentga qoldiradi. Baza ~20 dan oshsa bu
# ishlamay qoladi va retrieval sifatining o'zini tuzatish kerak bo'ladi
# (kuchliroq embedding modeli yoki kalit so'z + vektor gibrid qidiruvi).
FAQ_CANDIDATES = 5


class FaqService:
    async def search(self, query: str) -> list[dict]:
        """Chegaradan o'tgan FAQ nomzodlarini yaqinlik tartibida qaytaradi.

        BIR EMAS, BIR NECHTA nomzod qaytariladi. Sabab: embedding modeli
        parafrazlarga ~0.4-0.7 beradi va tartib ishonchsiz — to'g'ri javob
        ikkinchi o'rinda turishi mumkin. Ilgari faqat birinchi nomzod
        ishlatilardi va u noto'g'ri bo'lsa, undan keyingi TO'G'RI javob
        umuman ko'rilmasdi (2026-08-30 da aniqlandi: "AI loyihalaringiz
        qanday?" savoli CV FAQ iga mos kelib, AI FAQ i yetib bo'lmas edi).
        """
        try:
            embedding = get_embedder().embed_one(query)
            results = await chroma_client.query(
                query_embeddings=[embedding],
                n_results=FAQ_CANDIDATES,
                where={"type": "faq"},
            )
        except Exception as e:
            logger.warning(f"FAQ qidiruvda xato: {e}")
            return []

        docs = (results.get("documents") or [[]])[0]
        if not docs:
            return []

        metas = results["metadatas"][0]
        dists = results["distances"][0]

        candidates = []
        for doc, meta, dist in zip(docs, metas, dists):
            similarity = 1 - dist  # chroma masofani oshish tartibida beradi
            if similarity < FAQ_MATCH_THRESHOLD:
                continue
            candidates.append({
                "question": doc,
                "answer": meta.get("answer", ""),
                "faq_id": meta.get("faq_id"),
                "similarity": round(similarity, 4),
            })
        if not candidates:
            logger.debug("FAQ mos kelmadi (barcha nomzodlar chegaradan past)")
        return candidates

    async def search_best(self, query: str) -> dict | None:
        """Eng yaqin bitta nomzod (dashboard va testlar uchun qulaylik)."""
        candidates = await self.search(query)
        return candidates[0] if candidates else None

    async def try_answer(self, text: str, lang: str) -> str | None:
        """Bilim bazasidan javob topilsa qaytaradi, aks holda None.

        Barcha nomzodlar agentga BITTA chaqiruvda beriladi — u mos kelganini
        tanlaydi yoki hech biri to'g'ri kelmasa NO_ANSWER qaytaradi. Ketma-ket
        chaqirish ham mumkin edi, lekin u har xabarda bir necha LLM so'rovi
        degani bo'lardi: retrieval shovqinli, ya'ni chegaradan deyarli har doim
        kimdir o'tadi.
        """
        candidates = [c for c in await self.search(text) if c.get("answer")]
        if not candidates:
            return None

        answer = await faq_agent.generate(
            user_question=text,
            candidates=candidates,
            lang=lang,
        )
        if answer:
            logger.info(
                f"FAQ javobi berildi ({len(candidates)} nomzoddan; "
                f"eng yaqini: faq_id={candidates[0]['faq_id']}, "
                f"o'xshashlik={candidates[0]['similarity']})"
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
