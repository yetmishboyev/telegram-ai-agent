"""Ikkinchi miya — eslatmalarni saqlash, qidirish va qaytarish.

Saqlash xotira emas. Hech narsani unutmaydigan baza bir yildan keyin
qidirib bo'lmaydigan uyumga aylanadi — aynan shu yerda bunday loyihalar
o'ladi. Shuning uchun har eslatma SO'NADI, va har HAQIQIY teginish
so'nishni sekinlashtiradi (Ebbinghaus egri chizig'i).

    kuch      = asos_kun[kind] × (1 + ln(access_count))
    saqlanish = exp( −(bugun − last_touched) / kuch )        → 0..1

Ikki qaror `autograph` (MIT, manba g'oya) dan farq qiladi:

1. QATLAM USTUNDA SAQLANMAYDI. U yuqoridagi uch maydondan chiqadigan sof
   funksiya, ya'ni SQL da o'qish paytida hisoblanadi. Fayl bazasida tunlik
   qayta hisoblash kerak edi; PostgreSQLda kerak emas — bitta cron kamayadi
   va "qatlam eskirib qoldi" degan holat umuman paydo bo'lmaydi.

2. TEGINISH = ISHLATILGAN, TOPILGAN EMAS. Vektor moslik hisobni oshirmaydi:
   aks holda har qidiruvda hamma narsa "yangilanardi" va so'nish ma'nosini
   yo'qotardi. Faqat eslatma haqiqatan ishlatilganda sanaladi.
"""
import math
from datetime import datetime, timezone

from loguru import logger

# Turga qarab asosiy "yashash muddati" (kun). Odam haqidagi ma'lumot bir
# martalik fikrdan uzoq kerak bo'ladi.
BASE_DAYS: dict[str, int] = {
    "shaxs":     100,
    "loyiha":     90,
    "uchrashuv":  60,
    "maqola":     45,
    "fikr":       30,
}
DEFAULT_BASE_DAYS = 30

# Qatlam chegaralari (saqlanish qiymati bo'yicha)
TIER_ACTIVE = 0.70
TIER_WARM = 0.40
TIER_COLD = 0.15

VECTOR_TYPE = "note"


def strength(kind: str, access_count: int) -> float:
    """Eslatmaning "yashash kuchi" — kun hisobida."""
    base = BASE_DAYS.get(kind, DEFAULT_BASE_DAYS)
    return base * (1 + math.log(max(access_count, 1)))


def retention(kind: str, access_count: int, last_touched: datetime,
              now: datetime | None = None) -> float:
    """0..1 — eslatma qanchalik "yodda". 1 = hozirgina teginilgan."""
    now = now or datetime.now(timezone.utc)
    if last_touched.tzinfo is None:
        last_touched = last_touched.replace(tzinfo=timezone.utc)
    days = max((now - last_touched).total_seconds() / 86400, 0)
    return math.exp(-days / strength(kind, access_count))


def tier(kind: str, access_count: int, last_touched: datetime,
         pinned: bool = False, now: datetime | None = None) -> str:
    """Qatlam nomi. `pinned` hech qachon so'nmaydi."""
    if pinned:
        return "core"
    value = retention(kind, access_count, last_touched, now)
    if value >= TIER_ACTIVE:
        return "active"
    if value >= TIER_WARM:
        return "warm"
    if value >= TIER_COLD:
        return "cold"
    return "archive"


class NoteService:
    """Eslatmalarni saqlash, qidirish va qaytarish."""

    # ─── saqlash ──────────────────────────────────────────────────────────────

    async def save(
        self,
        body: str,
        source_kind: str = "matn",
        source_url: str | None = None,
    ) -> dict | None:
        """Matnni eslatma sifatida saqlaydi va vektorlaydi.

        LLM sarlavha, tur va qisqartma yozadi. Xato bo'lsa ham eslatma
        SAQLANADI — xom matn yo'qolgandan ko'ra sarlavhasiz saqlangani yaxshi.
        """
        body = (body or "").strip()
        if not body:
            return None

        meta = await self._describe(body)

        from app.database.models import Note
        from app.database.session import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            note = Note(
                kind=meta["kind"],
                title=meta["title"],
                body=body,
                summary=meta["summary"],
                source_kind=source_kind,
                source_url=source_url,
            )
            db.add(note)
            await db.commit()
            await db.refresh(note)
            note_id, title, kind, summary = note.id, note.title, note.kind, note.summary

        # Vektorlash — xato bo'lsa eslatma DB da qoladi, faqat qidiruvda topilmaydi
        try:
            vector_id = await self._index(note_id, title, body, summary)
            async with AsyncSessionLocal() as db:
                stored = await db.get(Note, note_id)
                if stored:
                    stored.vector_id = vector_id
                    await db.commit()
        except Exception as e:
            logger.error(f"Eslatmani vektorlashda xato (id={note_id}): {e}")

        logger.info(f"Eslatma saqlandi #{note_id} [{kind}]: {title[:60]}")
        return {"id": note_id, "title": title, "kind": kind, "summary": summary}

    async def _describe(self, body: str) -> dict:
        """LLM: sarlavha, tur, qisqartma. Xato bo'lsa oqilona standart."""
        from app.ai.agents.base_agent import BaseAgent
        from app.ai.agents.json_parse import parse_json_response
        from app.ai.models import ModelTier
        from app.ai.schemas import NOTE_META_SCHEMA

        class _Describer(BaseAgent):
            tier = ModelTier.FAST
            async def run(self, *a, **kw): return None

        prompt = (
            "Quyidagi eslatmani qisqacha tavsiflab ber.\n\n"
            f"ESLATMA:\n{body[:4000]}\n\n"
            "Qaytar:\n"
            "- title: 3-8 so'zli sarlavha (eslatmaning O'ZIDAN, to'qima emas)\n"
            "- kind: fikr | maqola | uchrashuv | shaxs | loyiha\n"
            "- summary: 1-2 gaplik qisqartma\n\n"
            "O'zbek lotin alifbosida. Faqat JSON."
        )
        fallback = {
            "title": body.split("\n")[0][:80] or "Eslatma",
            "kind": "fikr",
            "summary": None,
        }
        try:
            raw = await _Describer()._call_llm(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2, max_tokens=400,
                response_schema=NOTE_META_SCHEMA,
            )
            data = parse_json_response(raw)
        except Exception as e:
            logger.warning(f"Eslatma tavsifida xato — standart ishlatildi: {e}")
            return fallback

        if not isinstance(data, dict):
            return fallback
        kind = str(data.get("kind", "")).strip().lower()
        return {
            "title": (str(data.get("title", "")).strip() or fallback["title"])[:200],
            "kind": kind if kind in BASE_DAYS else "fikr",
            "summary": (str(data.get("summary", "")).strip() or None),
        }

    async def _index(self, note_id: int, title: str, body: str, summary: str | None) -> str:
        """Qidiruv uchun vektorlaydi. Sarlavha + qisqartma + matn birga."""
        from app.ai.rag.embedder import get_embedder
        from app.ai.vector_db.chroma_client import chroma_client

        doc = "\n".join(filter(None, [title, summary, body[:2000]]))
        doc_id = f"note_{note_id}"
        await chroma_client.upsert(
            ids=[doc_id],
            embeddings=[get_embedder().embed_one(doc)],
            documents=[doc],
            metadatas=[{"type": VECTOR_TYPE, "note_id": note_id, "title": title}],
        )
        return doc_id


    # ─── qidirish ─────────────────────────────────────────────────────────────

    async def search(self, query: str, limit: int = 5) -> list[dict]:
        """Savolga mos eslatmalarni topadi (eng yaqin birinchi).

        Topilish TEGINISH HISOBLANMAYDI — `access_count` oshirilmaydi. Aks
        holda har qidiruv butun bazani "yangilab", so'nishni bekor qilardi.
        Ishlatilganini `touch()` belgilaydi.
        """
        from sqlalchemy import select
        from app.ai.rag.embedder import get_embedder
        from app.ai.vector_db.chroma_client import chroma_client
        from app.database.models import Note
        from app.database.session import AsyncSessionLocal

        try:
            results = await chroma_client.query(
                query_embeddings=[get_embedder().embed_one(query)],
                n_results=limit,
                where={"type": VECTOR_TYPE},
            )
        except Exception as e:
            logger.warning(f"Eslatma qidiruvda xato: {e}")
            return []

        metas = (results.get("metadatas") or [[]])[0]
        dists = (results.get("distances") or [[]])[0]
        ids = [m.get("note_id") for m in metas if m.get("note_id")]
        if not ids:
            return []

        async with AsyncSessionLocal() as db:
            rows = {
                n.id: n for n in (await db.execute(
                    select(Note).where(Note.id.in_(ids))
                )).scalars().all()
            }

        found = []
        for meta, dist in zip(metas, dists):
            note = rows.get(meta.get("note_id"))
            if not note:
                continue
            found.append({
                "id": note.id,
                "title": note.title,
                "kind": note.kind,
                "summary": note.summary,
                "body": note.body,
                "created_at": note.created_at,
                "similarity": round(1 - dist, 3),
                "tier": tier(note.kind, note.access_count, note.last_touched, note.pinned),
            })
        return found

    async def touch(self, note_id: int) -> None:
        """Eslatma HAQIQATAN ishlatilganini belgilaydi — so'nish sekinlashadi."""
        from app.database.models import Note
        from app.database.session import AsyncSessionLocal

        try:
            async with AsyncSessionLocal() as db:
                note = await db.get(Note, note_id)
                if not note:
                    return
                note.access_count += 1
                note.last_touched = datetime.now(timezone.utc)
                await db.commit()
        except Exception as e:
            logger.warning(f"Eslatmaga teginishda xato (id={note_id}): {e}")

    # ─── qaytarish ────────────────────────────────────────────────────────────

    async def resurface(self, active: int = 2, archived: int = 1) -> list[dict]:
        """Brifing uchun eslatmalar: bir nechta faol + arxivdan tasodifiy bittasi.

        Arxivdan tasodifiy qaytarish — ikkinchi miyani arxivdan ajratib
        turadigan yagona narsa. Ba'zan shovqin, ba'zan esa unutib
        yuborilgan eng yaxshi fikr.
        """
        import random
        from sqlalchemy import select
        from app.database.models import Note
        from app.database.session import AsyncSessionLocal

        try:
            async with AsyncSessionLocal() as db:
                notes = (await db.execute(
                    select(Note).order_by(Note.last_touched.desc()).limit(300)
                )).scalars().all()
        except Exception as e:
            logger.warning(f"Eslatmalarni o'qishda xato: {e}")
            return []

        by_tier: dict[str, list] = {}
        for n in notes:
            by_tier.setdefault(
                tier(n.kind, n.access_count, n.last_touched, n.pinned), []
            ).append(n)

        picked = (by_tier.get("core", []) + by_tier.get("active", []))[:active]
        pool = by_tier.get("archive", []) or by_tier.get("cold", [])
        if pool and archived:
            picked += random.sample(pool, min(archived, len(pool)))

        return [
            {
                "id": n.id, "title": n.title, "kind": n.kind,
                "summary": n.summary or n.body[:160],
                "tier": tier(n.kind, n.access_count, n.last_touched, n.pinned),
                "created_at": n.created_at,
            }
            for n in picked
        ]

    async def stats(self) -> dict:
        """Qatlamlar bo'yicha taqsimot (bot menyusi uchun)."""
        from sqlalchemy import select
        from app.database.models import Note
        from app.database.session import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            notes = (await db.execute(select(Note))).scalars().all()

        counts: dict[str, int] = {}
        for n in notes:
            t = tier(n.kind, n.access_count, n.last_touched, n.pinned)
            counts[t] = counts.get(t, 0) + 1
        return {"jami": len(notes), "qatlamlar": counts}


note_service = NoteService()
