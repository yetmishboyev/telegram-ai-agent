import json
import re
import uuid
from datetime import datetime, timezone
from loguru import logger

from app.ai.agents.base_agent import BaseAgent
from app.ai.rag.embedder import get_embedder
from app.ai.vector_db.chroma_client import chroma_client

OWNER_USER_ID = -1  # Egani oddiy foydalanuvchilardan ajratish uchun
STYLE_DOC_TYPE = "owner_style"
MAX_STYLE_EXAMPLES = 5  # get_style_context() nechta namuna qaytarishi
MAX_STORED_STYLE_EXAMPLES = 50  # jami saqlanadigan namunalar chegarasi

# Juda qisqa xabar ("ok", "ha", "👍") uslub haqida hech narsa aytmaydi —
# faqat promptni to'ldiradi.
MIN_SAMPLE_LENGTH = 15

# Uslub kartasi (eganing yozish manerasining umumlashmasi) uchun sozlamalar
STYLE_CARD_KEY = "owner_style_card"
MIN_SAMPLES_FOR_CARD = 8       # shundan kam namunada karta ishonchsiz
CARD_REFRESH_EVERY = 10        # har shuncha yangi namunadan keyin qayta quriladi

# Hujjat so'rash xabarlari uslub namunasi sifatida SAQLANMAYDI. Sabab: ular
# ohang emas, MAZMUN sifatida o'zlashtirilib, agent notanish odamga "CV yoki
# ma'lumotlaringizni yuboring" deb yozib qo'yardi (2026-08 da aniqlangan).
_DOCUMENT_WORDS = ("cv", "rezyume", "obyektivka", "obiektivka", "hujjat", "ariza")
# "ma'lumot" o'zi juda keng tarqalgan so'z — faqat so'rov fe'li bilan birga filtrlanadi
_INFO_WORDS = ("ma'lumot", "malumot", "ma’lumot")
_REQUEST_VERBS = (
    "yubor", "tashla", "tashab", "jo'nat", "jonat", "bering", "bersin", "bera",
    "ayting", "ko'rsat", "korsat", "olib kel",
)
_WORD_RE = re.compile(r"[\w'’]+", re.UNICODE)
_URL_RE = re.compile(r"https?://\S+")


def _is_learnable(text: str) -> bool:
    """Xabar uslub namunasi sifatida saqlanishga yaroqlimi."""
    stripped = text.strip()
    if len(stripped) < MIN_SAMPLE_LENGTH:
        return False
    if stripped.startswith("/"):            # bot buyrug'i
        return False
    if not _URL_RE.sub("", stripped).strip():  # faqat havola
        return False

    lowered = stripped.lower()
    # O'zbekcha qo'shimchalar so'zga yopishadi ("hujjatlarni", "obyektivkangizni"),
    # shuning uchun aniq moslik emas, O'ZAK bo'yicha solishtiramiz.
    words = _WORD_RE.findall(lowered)
    if any(w.startswith(doc) for w in words for doc in _DOCUMENT_WORDS):
        logger.debug(f"Uslub namunasi o'tkazib yuborildi (hujjat so'rovi): {stripped[:60]!r}")
        return False
    if any(w in lowered for w in _INFO_WORDS) and any(v in lowered for v in _REQUEST_VERBS):
        logger.debug(f"Uslub namunasi o'tkazib yuborildi (ma'lumot so'rovi): {stripped[:60]!r}")
        return False
    return True


_STYLE_CARD_PROMPT = """Sen matn uslubini tahlil qiluvchi mutaxassissan. Quyida bitta insonning Telegram shaxsiy yozishmalaridagi xabarlari berilgan (---XABAR--- bilan ajratilgan).

Uning YOZISH MANERASINI tahlil qilib, boshqa muallif shu manerada yoza olishi uchun qisqa instruktsiya (uslub kartasi) yoz.

Qamrab ol:
- Ohang: rasmiy/samimiy, iliqlik darajasi, suhbatdoshga murojaat shakli (sen/siz)
- Gap uzunligi va tuzilishi, qisqartma va so'zlashuv shakllari ishlatiladimi
- Emoji va tinish belgilaridan foydalanish odati
- Takrorlanadigan iboralar, salomlashish va xayrlashish shakllari
- Qanday holatda qisqa, qanday holatda batafsil yozadi

MUHIM: xabarlarning MAZMUNI (kim haqida, qaysi ish yoki hujjat haqida ekani) seni qiziqtirmaydi — faqat YOZISH MANERASINI tavsiflab ber. Instruktsiyada aniq shaxs, tashkilot yoki hujjat nomlarini eslatma.

Instruktsiya 120-180 so'z, buyruq ohangida ("...yoz", "...ishlat"). Faqat instruktsiyani qaytar.

Xabarlar:
{samples}"""


class _StyleCardAgent(BaseAgent):
    """Eganing xabarlaridan uslub kartasini chiqaradi."""

    async def run(self, samples: list[str]) -> str | None:
        return await self.build(samples)

    async def build(self, samples: list[str]) -> str | None:
        joined = "\n\n---XABAR---\n\n".join(s[:400] for s in samples[:40])
        try:
            card = await self._call_llm(
                messages=[{"role": "user", "content": _STYLE_CARD_PROMPT.format(samples=joined)}],
                temperature=0.3,
                max_tokens=600,
            )
        except Exception as e:
            logger.warning(f"Uslub kartasini qurishda xato: {e}")
            return None
        return card.strip() or None


_style_card_agent = _StyleCardAgent()


class StyleLearner:
    """Eganing yozish uslubini o'rganadi va ChromaDB'da saqlaydi."""

    def __init__(self) -> None:
        self._embedder = get_embedder()
        self._card_cache: dict | None = None

    async def learn(self, owner_message: str) -> None:
        """Eganing xabarini uslub namunasi sifatida saqlaydi.

        Aynan bir xil matn allaqachon saqlangan bo'lsa o'tkazib yuboriladi
        (dedup), va jami namunalar soni `MAX_STORED_STYLE_EXAMPLES`dan
        oshsa, eng eskisi o'chiriladi (roadmap Faza 3, band 7).
        Sifat darvozasi uchun `_is_learnable`ga qarang.
        """
        text = owner_message.strip()
        if not _is_learnable(text):
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

            await self._maybe_refresh_card(len(all_entries))
        except Exception as e:
            logger.warning(f"Uslub saqlashda xato: {e}")

    # ─── uslub kartasi (manerani umumlashtirish) ──────────────────────────────

    async def _load_card(self) -> dict | None:
        """Uslub kartasini keshdan yoki AgentConfig'dan o'qiydi."""
        if self._card_cache is not None:
            return self._card_cache
        try:
            from sqlalchemy import select
            from app.database.session import AsyncSessionLocal
            from app.database.models import AgentConfig
            async with AsyncSessionLocal() as db:
                r = await db.execute(
                    select(AgentConfig).where(AgentConfig.key == STYLE_CARD_KEY)
                )
                cfg = r.scalar_one_or_none()
            if cfg:
                self._card_cache = json.loads(cfg.value)
        except Exception as e:
            logger.debug(f"Uslub kartasini o'qishda xato: {e}")
        return self._card_cache

    async def _save_card(self, card: str, sample_count: int) -> None:
        payload = json.dumps({
            "card": card,
            "sample_count": sample_count,
            "built_at": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False)
        try:
            from sqlalchemy import select
            from app.database.session import AsyncSessionLocal
            from app.database.models import AgentConfig
            async with AsyncSessionLocal() as db:
                r = await db.execute(
                    select(AgentConfig).where(AgentConfig.key == STYLE_CARD_KEY)
                )
                cfg = r.scalar_one_or_none()
                if cfg:
                    cfg.value = payload
                else:
                    db.add(AgentConfig(
                        key=STYLE_CARD_KEY, value=payload,
                        description="Eganing shaxsiy yozishmalaridan o'rganilgan uslub kartasi",
                    ))
                await db.commit()
            self._card_cache = json.loads(payload)
            logger.info(f"Eganing uslub kartasi yangilandi ({sample_count} ta namuna asosida)")
        except Exception as e:
            logger.warning(f"Uslub kartasini saqlashda xato: {e}")

    async def _maybe_refresh_card(self, sample_count: int) -> None:
        """Namunalar yetarli darajada yangilangan bo'lsa kartani qayta quradi."""
        if sample_count < MIN_SAMPLES_FOR_CARD:
            return
        card = await self._load_card()
        built_at_count = (card or {}).get("sample_count", 0)
        if card and sample_count - built_at_count < CARD_REFRESH_EVERY:
            return
        await self.rebuild_card(sample_count)

    async def rebuild_card(self, sample_count: int | None = None) -> str | None:
        """Saqlangan barcha namunalardan uslub kartasini qayta quradi."""
        try:
            stored = await chroma_client.get(
                where={"type": STYLE_DOC_TYPE}, include=["documents"]
            )
            samples = [d for d in stored.get("documents", []) if d and d.strip()]
        except Exception as e:
            logger.warning(f"Uslub namunalarini o'qishda xato: {e}")
            return None
        if len(samples) < MIN_SAMPLES_FOR_CARD:
            return None
        card = await _style_card_agent.build(samples)
        if card:
            await self._save_card(card, sample_count or len(samples))
        return card

    # ─── promptga beriladigan kontekst ────────────────────────────────────────

    async def get_style_context(self, query: str) -> str:
        """Eganing uslubini promptga beriladigan blok ko'rinishida qaytaradi.

        Blok ikki qismdan iborat: umumlashtirilgan uslub kartasi (bo'lsa) va
        bir nechta jonli namuna. Namunalar oldida OHANG-ONLY ogohlantirishi
        turadi — busiz model namunalarning MAZMUNINI ko'chirib, notanish
        odamga eganing boshqa suhbatdagi iltimosini yozib qo'yardi.
        """
        try:
            embedding = self._embedder.embed_one(query)
            results = await chroma_client.query(
                query_embeddings=[embedding],
                n_results=MAX_STYLE_EXAMPLES,
                where={"type": STYLE_DOC_TYPE},
            )
            docs = results.get("documents", [[]])[0]
            examples = [d for d in docs if d.strip()][:3]
        except Exception as e:
            logger.debug(f"Uslub konteksti olishda xato: {e}")
            examples = []

        card_data = await self._load_card()
        card = (card_data or {}).get("card")

        if not card and not examples:
            return ""

        lines = ["## Eganing yozish uslubi (o'rganilgan) — FAQAT OHANG uchun:"]
        if card:
            lines.append(card)
        if examples:
            lines.append(
                "\nQuyidagi namunalar eganing BOSHQA odamlarga, boshqa vaziyatda "
                "yozgan xabarlari. Ulardagi mazmunni, iltimos yoki so'rovlarni "
                "TAKRORLAMA (masalan hujjat, CV yoki ma'lumot so'rama) — faqat "
                "ohang, gap qurilishi va so'z tanlashiga ergash:"
            )
            for ex in examples:
                lines.append(f"- {ex}")
        return "\n".join(lines)


style_learner = StyleLearner()
