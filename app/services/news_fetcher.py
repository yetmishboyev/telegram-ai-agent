import httpx
import json
import xml.etree.ElementTree as ET
from loguru import logger

from app.ai.agents.base_agent import BaseAgent
from app.utils.uz_text import to_latin_uz


# Sun'iy intellektga oid RSS manbalar — nufuzli, mustaqil saytlar.
# Har biri jonli tekshirilgan (2026-07); ishlamay qolsa fetch_rss jimgina o'tkazadi.
AI_NEWS_FEEDS = [
    "https://news.google.com/rss/search?q=artificial+intelligence+AI&hl=en-US&gl=US&ceid=US:en",
    "https://venturebeat.com/category/ai/feed/",
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://huggingface.co/blog/feed.xml",
    "https://www.technologyreview.com/feed/",          # MIT Technology Review
    "https://www.wired.com/feed/tag/ai/latest/rss",    # Wired AI
    "https://feeds.arstechnica.com/arstechnica/index", # Ars Technica
    "https://blog.google/technology/ai/rss/",          # Google AI Blog (birlamchi manba)
    "https://the-decoder.com/feed/",                   # The Decoder (AI-fokus)
]

# Mashhur AI/texnologiya Telegram kanallari — userbot o'qiydi (jonli tekshirilgan).
NEWS_TG_CHANNELS = [
    "@ai_newz",
    "@seeallochnaya",
    "@data_secrets",
]

# Faqat shu oynadagi yangiliklar "so'nggi" hisoblanadi
NEWS_FRESHNESS_HOURS = 48

# 09:00 dagi ta'limiy postlar uchun mavzular (rotatsiya)
EDUCATIONAL_TOPICS = [
    "Large Language Model (LLM)",
    "Retrieval-Augmented Generation (RAG)",
    "Prompt Engineering",
    "Neural Network",
    "Machine Learning vs Deep Learning",
    "Transformer arxitekturasi",
    "AI Agent nima",
    "Embeddings va vektorli qidiruv",
    "Fine-tuning nima",
    "AI hallucination muammosi",
    "Reinforcement Learning from Human Feedback (RLHF)",
    "Multimodal AI",
    "AI inference va training farqi",
    "Zero-shot va Few-shot learning",
    "Computer Vision asoslari",
    "Natural Language Processing (NLP)",
    "AI da bias muammosi",
    "Autonomous AI Agent",
    "Model Context Protocol (MCP)",
    "AI xavfsizligi va alignment",
    "Generative AI nima",
    "Diffusion modellari",
    "OpenAI, Anthropic, Google — farqlari",
    "AI da tokenizatsiya",
    "Knowledge Graph nima",
]


# Amaliy qo'llanma postlari uchun mavzular (rotatsiya) — eng ulashiladigan format
PRACTICAL_TOPICS = [
    "ChatGPT bilan professional CV yozish",
    "AI yordamida ish e'lonlariga cover letter tayyorlash",
    "ChatGPT'dan ingliz tilini o'rganishda foydalanish",
    "AI bilan taqdimot (slaydlar) tayyorlash",
    "Telegram kanal uchun AI bilan kontent reja tuzish",
    "AI yordamida email yozishmalarini professional qilish",
    "ChatGPT bilan biznes g'oyani tekshirish",
    "AI bilan hujjat va shartnomalarni tahlil qilish",
    "Excel/Sheets ishlarini AI bilan avtomatlashtirish",
    "AI yordamida o'quv reja (study plan) tuzish",
    "Prompt yozishning amaliy formulasi",
    "AI bilan ijtimoiy tarmoq postlarini yozish",
]

# AI vosita sharhlari uchun ro'yxat (rotatsiya)
AI_TOOLS = [
    "ChatGPT (OpenAI)",
    "Claude (Anthropic)",
    "Gemini (Google)",
    "Midjourney — rasm generatsiya",
    "Notion AI — eslatma va hujjatlar",
    "Perplexity — AI qidiruv",
    "ElevenLabs — ovoz generatsiya",
    "Cursor — AI kod muharriri",
    "Canva Magic Studio — dizayn",
    "Suno — musiqa generatsiya",
    "NotebookLM — hujjat tahlili",
    "Grammarly — yozishma tahriri",
]

# Post uslublari — ega har post uchun tanlaydi (bot menyusidan).
POST_STYLES: dict[str, dict] = {
    "jonli": {
        "label": "💬 Jonli-professional",
        "instruction": (
            "USLUB — JONLI-PROFESSIONAL: qiziqarli, ammo vazmin va professional ohangda "
            "yoz. O'quvchiga faqat \"siz\" deb murojaat qil. Boshlanish o'quvchini "
            "qiziqtirsin (savol, kutilmagan fakt yoki hayotiy holat), lekin shov-shuv va "
            "keskin da'volardan qoch. Hazil va ko'cha tilini ishlatma — iliq, ishonchli, "
            "adabiy o'zbek tili. Fikrlar aniq, gaplar ravon; qisqa va o'rtacha gaplarni "
            "navbatlashtir. Undov belgisini kamdan-kam ishlat. Emoji juda me'yorida "
            "(post boshida va ehtimol bo'limlarda 1-2 ta). Yakunda o'quvchini mulohazaga "
            "undaydigan bosiq savol yoki taklif bilan tugat."
        ),
    },
    "chapani": {
        "label": "🔥 Chapani",
        "instruction": (
            "USLUB — CHAPANI: topvaroq, jonli, ko'chaning tirik tilida yoz. Boshida "
            "diqqatni darrov ilib oladigan o'tkir ochilish (hook) ber. O'quvchiga "
            "do'stona, samimiy murojaat qil, ritorik savol va o'rinli hazil ishlat. "
            "Quruq akademik ohangdan qoch — lekin mazmun aniq va to'g'ri bo'lsin. "
            "Oxirida o'quvchini fikr yozishga yoki ulashishga undaydigan jonli chaqiruv ber."
        ),
    },
    "expert": {
        "label": "🎩 Rasmiy-ekspert",
        "instruction": (
            "USLUB — EKSPERT: ishonchli, professional lekin iliq ohang. Puxta, vazmin, "
            "adabiy o'zbek tili. Aniqlik va chuqurlik birinchi o'rinda."
        ),
    },
    "qisqa": {
        "label": "⚡ Qisqa-lo'nda",
        "instruction": (
            "USLUB — QISQA: keraksiz gaplarsiz, faqat mag'zini yoz. Qisqa jumlalar, "
            "kerak bo'lsa ro'yxat. 120-160 so'z. Har bir gap qiymat bersin."
        ),
    },
}
# Egaga chapani juda topvaroq tuyuldi (2026-07-16) — jadval postlari endi
# jonli-professional uslubda chiqadi; chapani menyuda tanlov sifatida qoladi.
DEFAULT_STYLE = "jonli"


_LATIN_RULE = (
    " MUHIM: butun matnni FAQAT o'zbek LOTIN alifbosida yoz — biror so'zga ham "
    "krill harflarini (а, б, в, г, д...) aralashtirma."
)


def style_instruction(style: str) -> str:
    return POST_STYLES.get(style, POST_STYLES[DEFAULT_STYLE])["instruction"] + _LATIN_RULE


class NewsFetcher(BaseAgent):

    def __init__(self) -> None:
        super().__init__()
        # Namuna kanaldan o'rganilgan uslub (AgentConfig'dan lazy yuklanadi)
        self._learned_style: dict | None = None

    async def run(self, *args, **kwargs):
        pass

    # ─── o'rganilgan uslub ─────────────────────────────────────────────────────

    def set_learned_style_cache(self, data: dict | None) -> None:
        self._learned_style = data

    async def get_learned_style(self) -> dict | None:
        """O'rganilgan uslubni keshdan yoki DB (AgentConfig) dan oladi."""
        if self._learned_style is not None:
            return self._learned_style
        try:
            from sqlalchemy import select
            from app.database.session import AsyncSessionLocal
            from app.database.models import AgentConfig
            async with AsyncSessionLocal() as db:
                r = await db.execute(
                    select(AgentConfig).where(AgentConfig.key == "learned_style")
                )
                cfg = r.scalar_one_or_none()
            if cfg:
                self._learned_style = json.loads(cfg.value)
        except Exception as e:
            logger.warning(f"O'rganilgan uslubni o'qishda xato: {e}")
        return self._learned_style

    async def analyze_style(self, posts_text: str) -> str | None:
        """Namuna postlardan uslub kartasini (yozish instruktsiyasini) chiqaradi."""
        prompt = f"""Sen matn uslubini tahlil qiluvchi mutaxassissan. Quyida bitta Telegram kanalning postlari berilgan (---POST--- bilan ajratilgan). Ularning YOZISH USLUBINI tahlil qilib, boshqa muallif shu uslubda yoza olishi uchun ANIQ instruktsiya (uslub kartasi) yoz.

Qamrab ol:
- Ohang va munosabat (rasmiy/samimiy, o'quvchiga murojaat shakli — sen/siz)
- Post qanday OCHILADI (hook turi) va qanday YOPILADI (CTA bormi, qanaqa)
- Tuzilish: xatboshi uzunligi, ro'yxat/raqamlash ishlatilishi, ajratish belgilari
- Gap uslubi: uzun/qisqa, savollar, undovlar
- Emoji: qanchalik ko'p, qayerda
- Formatlash: qalin/kursiv nimalar uchun
- O'ziga xos iboralar, takrorlanadigan elementlar

Instruktsiya 150-250 so'z, buyruq ohangida ("...yoz", "...ishlat") bo'lsin. Faqat instruktsiyani qaytar.

Postlar:
{posts_text[:9000]}"""
        try:
            result = await self._call_llm(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3, max_tokens=800,
            )
            return result.strip() or None
        except Exception as e:
            logger.error(f"Uslub tahlilida xato: {e}")
            return None

    async def _style_text(self, style: str) -> str:
        """Generatorlar uchun uslub instruktsiyasi — 'learned' dinamik, qolganlari statik."""
        if style == "learned":
            data = await self.get_learned_style()
            if data:
                samples = "\n\n---NAMUNA---\n\n".join(s[:700] for s in data.get("samples", [])[:2])
                return (
                    f"USLUB — O'RGANILGAN ({data.get('source', '')} kanalidan):\n"
                    f"{data['style_card']}\n\n"
                    f"Quyidagi namunalar USLUB uchun (mazmunini KO'CHIRMA, faqat ohang/tuzilishga ergash). "
                    f"Namuna kanalning nomi, @username yoki imzosini postga HECH QACHON qo'shma:\n"
                    f"{samples}" + _LATIN_RULE
                )
            logger.warning("O'rganilgan uslub topilmadi — standart uslub ishlatiladi")
        return style_instruction(style)

    async def _call_llm(self, *args, **kwargs) -> str:
        """Post generatsiyasi natijasini lotinlashtiradi — LLM ba'zan lotincha
        o'zbek matniga krill harflarni aralashtirib yuboradi (masalan "qidirади").
        Shuningdek markdown-escape qilingan hashtaglarni (\\#) va (uslub o'rganilgan
        bo'lsa) namuna kanalning imzosini deterministik tozalaydi — prompt qoidasi
        yetarli emas, model manba nomini ko'rib postga qo'shib yuborishi mumkin."""
        import re as _re
        result = await super()._call_llm(*args, **kwargs)
        result = to_latin_uz(result).replace("\\#", "#")
        source = (self._learned_style or {}).get("source")
        if source and source.lower() in result.lower():
            # imzo qatori bo'lsa qatorni, matn ichida bo'lsa faqat mention'ni olamiz
            result = _re.sub(rf"^\W*{_re.escape(source)}\W*$", "", result,
                             flags=_re.IGNORECASE | _re.MULTILINE)
            result = _re.sub(_re.escape(source), "", result, flags=_re.IGNORECASE)
            result = _re.sub(r"\n{3,}", "\n\n", result).strip()
        return result

    @staticmethod
    def _is_fresh(pub_date: str, hours: int = NEWS_FRESHNESS_HOURS) -> bool:
        """pubDate so'nggi `hours` ichidami. Parse bo'lmasa True (item tashlanmaydi)."""
        if not pub_date:
            return True
        try:
            from email.utils import parsedate_to_datetime
            from datetime import datetime, timezone, timedelta
            dt = parsedate_to_datetime(pub_date)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt >= datetime.now(timezone.utc) - timedelta(hours=hours)
        except Exception:
            return True

    async def fetch_rss(self, url: str, limit: int = 5) -> list[dict]:
        """RSS feeddan yangiliklar oladi (faqat so'nggi NEWS_FRESHNESS_HOURS ichidagilar)."""
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()
        except Exception as e:
            logger.warning(f"RSS fetch xatosi ({url}): {e}")
            return []

        items = []
        try:
            root = ET.fromstring(resp.text)
            channel = root.find("channel")
            if channel is None:
                return []
            for item in channel.findall("item"):
                if len(items) >= limit:
                    break
                title = item.findtext("title", "").strip()
                link = item.findtext("link", "").strip()
                desc = item.findtext("description", "").strip()
                pub = item.findtext("pubDate", "").strip()
                # HTML teglarini tozalash
                import re
                desc = re.sub(r"<[^>]+>", "", desc)[:300]
                if title and self._is_fresh(pub):
                    from urllib.parse import urlparse
                    domain = urlparse(url).netloc.removeprefix("www.").removeprefix("feeds.")
                    items.append({"title": title, "link": link, "desc": desc, "source": domain})
        except Exception as e:
            logger.warning(f"RSS parse xatosi: {e}")

        return items

    async def fetch_telegram_news(
        self, hours: int = NEWS_FRESHNESS_HOURS, per_channel: int = 20
    ) -> list[dict]:
        """Mashhur AI Telegram kanallaridan so'nggi matnli postlarni oladi."""
        from datetime import datetime, timezone, timedelta
        from app.services.telegram_service import telegram_service

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        items: list[dict] = []
        for ch in NEWS_TG_CHANNELS:
            try:
                msgs = await telegram_service._client.get_messages(ch, limit=per_channel)
            except Exception as e:
                logger.warning(f"TG yangilik manbasi o'qilmadi ({ch}): {e}")
                continue
            for m in msgs:
                if not (m and m.text and len(m.text) > 150 and m.date and m.date > cutoff):
                    continue
                first_line = m.text.strip().split("\n")[0][:120].strip("*_#")
                items.append({
                    "title": first_line,
                    "desc": m.text[len(first_line):500].strip(),
                    "link": f"https://t.me/{ch.lstrip('@')}/{m.id}",
                    "source": ch,
                })
        logger.debug(f"TG yangiliklari: {len(items)} ta ({len(NEWS_TG_CHANNELS)} kanaldan)")
        return items

    async def get_ai_news(self, count: int = 3) -> list[dict]:
        """RSS saytlar + Telegram kanallardan yangilik yig'ib, dedup qilib qaytaradi."""
        all_items: list[dict] = []
        for feed_url in AI_NEWS_FEEDS:
            items = await self.fetch_rss(feed_url, limit=6)
            all_items.extend(items)

        try:
            all_items.extend(await self.fetch_telegram_news())
        except Exception as e:
            logger.warning(f"TG yangiliklarini olishda xato: {e}")

        # Takrorlanganlarni olib tashlash (sarlavha bo'yicha)
        seen, unique = set(), []
        for item in all_items:
            key = item["title"][:60].lower()
            if key not in seen:
                seen.add(key)
                unique.append(item)

        # Manbalar bo'yicha round-robin — aks holda ro'yxat boshidagi feedlar
        # count'ni to'ldirib, keyingi manbalar (jumladan TG kanallar) kesilib qoladi.
        by_source: dict[str, list[dict]] = {}
        for item in unique:
            by_source.setdefault(item.get("source", "?"), []).append(item)
        interleaved: list[dict] = []
        while len(interleaved) < count and any(by_source.values()):
            for src in list(by_source.keys()):
                if by_source[src]:
                    interleaved.append(by_source[src].pop(0))
                    if len(interleaved) >= count:
                        break
        return interleaved

    async def generate_educational_post(self, topic: str, style: str = DEFAULT_STYLE) -> str:
        """Berilgan AI mavzuda o'zbek tilida ta'limiy post yaratadi."""
        style_text = await self._style_text(style)
        prompt = f"""
Sen sun'iy intellekt sohasida 8-10 yillik tajribaga ega, o'z auditoriyasiga ega bo'lgan ekspert-muallifsan. Telegram kanalingga quyidagi mavzu bo'yicha post yozasan.

Mavzu: {topic}

{style_text}

Talablar:
- Jonli inson yozgandek yoz — quruq, shablon yoki "AI yozgan" his qildiradigan ohangdan qoch. Xuddi tanishingga tushuntirayotgandek, samimiy va qiziqarli tarzda yoz.
- O'zingning fikringni, kichik bir kuzatuvingni yoki qiziqarli detalni qo'sh — faqat quruq ta'rif bo'lmasin.
- To'g'ri adabiy o'zbek tilida yoz. Grammatika xatolariga yo'l qo'yma.
- So'zlarni to'g'ri qo'lla: "sun'iy intellekt" (suniy emas), "dastur" (dasturiy ta'minot), "foydalanuvchi" va hokazo.
- Emojilarni faqat sarlavhalarda emas, matn ichida ham joyida va tabiiy ishlat (haddan tashqari ko'p bo'lmasin — mazmunni jonlantirish uchun).
- Telegram Markdown formatida yoz (**qalin**, _kursiv_).
- Hajmi: 220–280 so'z.
- Erkin tuzilishda yoz, lekin quyidagi mantiqiy oqimga amal qil (sarlavhalarni so'zma-so'z takrorlama, har safar biroz boshqacha ifodala):

🎓 **[Mavzu sarlavhasi — jozibali va qiziqtiradigan]**

[Nima bu — oddiy tilda, kundalik hayot yoki tanish misol bilan boshlab tushuntir]

[Qanday ishlaydi — qisqa, tushunarli, kerak bo'lsa o'xshatish bilan]

[Qayerda qo'llaniladi — 2–3 ta real hayotiy misol]

[Nega bu muhim — o'z fikring yoki qisqa xulosa bilan yakunla]

#SuniyIntellekt #AI #Texnologiya #Dars
"""
        return await self._call_llm(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=700,
        )

    async def curate_top_news(self, items: list[dict]) -> dict | None:
        """Yangiliklar ichidan auditoriya uchun ENG muhimini tanlaydi (curation).

        Returns: {item, reason} yoki None (ro'yxat bo'sh bo'lsa).
        """
        from app.ai.agents.json_parse import parse_json_response

        if not items:
            return None
        if len(items) == 1:
            return {"item": items[0], "reason": ""}

        listing = "\n".join(
            f"{i}. [{it.get('source', 'sayt')}] {it['title']}\n   {it.get('desc', '')[:150]}"
            for i, it in enumerate(items)
        )
        prompt = f"""Sen sun'iy intellekt mavzusidagi o'zbek Telegram kanalining bosh muharririsan. Auditoriya: texnologiyaga qiziquvchi oddiy foydalanuvchilar va IT mutaxassislar.

Quyidagi yangiliklar ichidan kanal uchun ENG qimmatli BITTASINI tanla — auditoriya hayotiga eng ko'p ta'sir qiladigani yoki eng qiziqarli muhokama uyg'otadigani:

{listing}

Faqat JSON qaytar: {{"index": <raqam>, "reason": "nega aynan shu — 1 gap"}}"""

        try:
            raw = await self._call_llm(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3, max_tokens=200,
            )
            data = parse_json_response(raw)
            idx = int(data.get("index", 0))
            if not (0 <= idx < len(items)):
                idx = 0
            return {"item": items[idx], "reason": str(data.get("reason", ""))[:200]}
        except Exception as e:
            logger.warning(f"Curation xatosi — birinchi yangilik olinadi: {e}")
            return {"item": items[0], "reason": ""}

    async def generate_deep_news_post(
        self, item: dict, style: str = DEFAULT_STYLE, curation_reason: str = ""
    ) -> str:
        """BITTA yangilikka chuqur tahlil posti — professional format.

        Eski "3 ta yangilik × 2-3 gap" digest o'rniga: hook → fakt → nega muhim
        → bizga nima anglatadi → xulosa/CTA.
        """
        reason_line = f"\nMuharrir izohi (nega tanlangan): {curation_reason}" if curation_reason else ""
        style_text = await self._style_text(style)
        prompt = f"""Sen sun'iy intellekt sohasini chuqur biladigan, o'z auditoriyasiga ega tahlilchi-muallifsan. Quyidagi BITTA yangilik asosida Telegram kanalga o'zbek tilida CHUQUR TAHLILIY post yoz.

Yangilik (inglizcha manba):
Sarlavha: {item.get('title', '')}
Tavsif: {item.get('desc', '')}
Havola: {item.get('link', '')}{reason_line}

{style_text}

Post tuzilishi (sarlavhalarni yozma, tabiiy oqim bo'lsin):
1. HOOK — birinchi 1-2 gap o'quvchini to'xtatsin (savol, kutilmagan fakt yoki natija).
2. NIMA BO'LDI — aniq faktlar: kim, nima, qachon. Taxmin qo'shma, manbada bo'lmagan raqam to'qima.
3. NEGA BU MUHIM — sening tahliling: bu sohani/bozorni qanday o'zgartiradi.
4. BIZGA NIMA ANGLATADI — O'zbekistondagi oddiy foydalanuvchi yoki mutaxassis uchun amaliy ma'nosi.
5. XULOSA + savol yoki CTA — o'quvchini fikr bildirishга undaydigan yakun.

Qoidalar:
- BITTA yangilik, chuqur — boshqa yangiliklar haqida yozma.
- 180-260 so'z. Telegram Markdown (**qalin**, _kursiv_). Emoji tabiiy, me'yorida.
- Sarlavha qatori bilan boshla: emoji + **qalin sarlavha** (o'zbekcha, jozibali).
- Oxiriga 3-4 ta hashtag. Kanal linkini QO'SHMA.
"""
        return await self._call_llm(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=900,
        )

    async def generate_growth_strategy(self, stats: dict) -> dict | None:
        """Kanal statistikasi asosida o'sish strategiyasi tuzadi (dashboard uchun).

        Returns: {holat, tavsiyalar[], kontent_goyalar[], keyingi_qadam} yoki None.
        """
        from app.ai.agents.json_parse import parse_json_response

        prompt = f"""Sen Telegram kanallarini o'stirish bo'yicha tajribali SMM strategisan. Quyida "@Yetmishboyev_Sh" (sun'iy intellekt mavzusidagi o'zbek tilidagi kanal) statistikasi berilgan. Uni tahlil qilib, obunachilarni ko'paytirish strategiyasini tuz.

Statistika (JSON):
{json.dumps(stats, ensure_ascii=False, default=str)}

Faqat JSON qaytar:
{{
  "holat": "kanalning hozirgi holati haqida 2-3 gaplik xolis tahlil",
  "tavsiyalar": ["aniq, bajarsa bo'ladigan tavsiya (4-6 ta)", "..."],
  "kontent_goyalar": ["keyingi hafta uchun aniq post g'oyasi (4-5 ta, mavzu + format)", "..."],
  "keyingi_qadam": "eng birinchi qilinishi kerak bo'lgan BITTA amal"
}}

Qoidalar: umumiy gap emas, shu statistikaga bog'langan aniq tavsiyalar ber (masalan qaysi post turi yaxshi ishlayapti — o'shani ko'paytirish, qaysi vaqt/format sinash kerak). Har bir tavsiya/g'oya 1-2 gapdan oshmasin. O'zbek LOTIN alifbosida. Faqat JSON, izohsiz."""

        try:
            raw = await self._call_llm(
                messages=[{"role": "user", "content": prompt}],
                # 900 yetmasdi — o'zbekcha to'liq strategiya kesilib, JSON buzilardi
                temperature=0.5, max_tokens=2000,
            )
            data = parse_json_response(raw)
        except Exception as e:
            logger.error(f"Strategiya generatsiyada xato: {e}")
            return None

        if not isinstance(data, dict) or not data.get("holat"):
            return None
        return {
            "holat": str(data.get("holat", ""))[:600],
            "tavsiyalar": [str(t)[:300] for t in (data.get("tavsiyalar") or [])][:6],
            "kontent_goyalar": [str(g)[:300] for g in (data.get("kontent_goyalar") or [])][:5],
            "keyingi_qadam": str(data.get("keyingi_qadam", ""))[:300],
        }

    async def generate_quiz(self, topic: str, kind: str = "quiz") -> dict | None:
        """Interaktiv quiz (to'g'ri javobli) yoki so'rovnoma (fikr) generatsiya qiladi.

        Returns: {kind, question, options[], correct_index?, explanation?} yoki None.
        """
        from app.ai.agents.json_parse import parse_json_response

        if kind == "quiz":
            prompt = f"""Sen sun'iy intellekt bo'yicha Telegram kanal muallifisan. "{topic}" mavzusida obunachilar uchun QIZIQARLI test savoli (quiz) tuz.

Faqat JSON qaytar:
{{"question": "savol matni", "options": ["variant A", "variant B", "variant C", "variant D"], "correct_index": 0, "explanation": "to'g'ri javob nega to'g'ri — 1-2 gap"}}

Qoidalar: 3-4 variant bo'lsin, faqat BITTA to'g'ri; savol qiziqarli va aniq; hammasi o'zbek LOTIN alifbosida; savol 250 belgidan, har variant 100 belgidan oshmasin. Faqat JSON."""
        else:  # so'rovnoma (opinion poll — to'g'ri javob yo'q)
            prompt = f"""Sen sun'iy intellekt bo'yicha Telegram kanal muallifisan. "{topic}" mavzusida obunachilar fikrini so'raydigan qiziqarli SO'ROVNOMA (poll) tuz.

Faqat JSON qaytar:
{{"question": "so'rov savoli", "options": ["variant A", "variant B", "variant C", "variant D"]}}

Qoidalar: 2-4 variant; fikr/tanlov so'rovi (to'g'ri javob yo'q); hammasi o'zbek LOTIN alifbosida; savol 250 belgidan, har variant 100 belgidan oshmasin. Faqat JSON."""

        try:
            raw = await self._call_llm(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.6, max_tokens=500,
            )
            data = parse_json_response(raw)
        except Exception as e:
            logger.error(f"Quiz generatsiyada xato: {e}")
            return None

        if not isinstance(data, dict):
            return None
        question = str(data.get("question", "")).strip()[:250]
        options = [str(o).strip()[:100] for o in (data.get("options") or []) if str(o).strip()]
        options = options[:4]
        if not question or len(options) < 2:
            return None

        result: dict = {"kind": kind, "question": question, "options": options}
        if kind == "quiz":
            ci = data.get("correct_index", 0)
            result["correct_index"] = ci if isinstance(ci, int) and 0 <= ci < len(options) else 0
            result["explanation"] = str(data.get("explanation", "")).strip()[:200]
        return result

    async def generate_free_post(self, topic: str, style: str = DEFAULT_STYLE) -> str:
        """Ega bergan erkin mavzu/topshiriq bo'yicha post yozadi."""
        style_text = await self._style_text(style)
        prompt = f"""
Sen o'z auditoriyasiga ega, tajribali Telegram kanal muallifisan. Quyidagi mavzu/topshiriq bo'yicha kanalingga o'zbek tilida post yoz.

Mavzu / topshiriq: {topic}

{style_text}

Talablar:
- To'g'ri adabiy o'zbek tilida, grammatikaga e'tibor berib yoz.
- Emojilarni matn ichida tabiiy ishlat (haddan oshirmasdan).
- Telegram Markdown formatida yoz (**qalin**, _kursiv_).
- Hajmi mavzuga mos bo'lsin: 150-280 so'z.
- Oxiriga kanal linkini QO'SHMA — u avtomatik qo'shiladi.
"""
        return await self._call_llm(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.75,
            max_tokens=800,
        )

    async def regenerate_post(
        self,
        original_text: str,
        feedback: str,
        post_type: str,
        topic: str = "",
    ) -> str:
        """Feedbackka asosan postni qayta tayyorlaydi."""
        prompt = f"""
Quyida Telegram kanal uchun tayyorlangan post va ega tomonidan berilgan izoh bor.
Izohga asosan postni qayta yoz. O'zbek tili grammatikasiga e'tibor ber.

Mavjud post:
{original_text}

Ega izohi (nima o'zgartirilsin):
{feedback}

Xuddi shu formatda, lekin izohga ko'ra tahrirlab, to'liq yangi postni qaytargin.
Oxiridagi "—" va kanal link qatorlarini OLIB TASHLASH kerak — ular keyinchalik avtomatik qo'shiladi.
"""
        return await self._call_llm(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
            max_tokens=800,
        )

    async def generate_practical_post(self, topic: str, style: str = DEFAULT_STYLE) -> str:
        """Amaliy qo'llanma posti — o'quvchi darhol qo'llay oladigan qadamlar."""
        style_text = await self._style_text(style)
        prompt = f"""Sen AI vositalaridan kundalik ishda foydalanadigan amaliyotchi mutaxassissan. Telegram kanalga quyidagi mavzuda AMALIY QO'LLANMA posti yoz — o'quvchi o'qib bo'lgach darhol o'zi qilib ko'ra olsin.

Mavzu: {topic}

{style_text}

Tuzilish:
- Sarlavha: emoji + **qalin** — natijani va'da qilsin ("... 5 daqiqada", "... 3 qadamda").
- Kirish 1-2 gap: bu nima uchun kerak, qanday muammoni yechadi.
- 3-5 ta RAQAMLANGAN qadam. Har qadamda ANIQ amal — kerak bo'lsa aynan yozadigan prompt namunasini _kursiv_ da ber.
- Yakun: kichik pro-maslahat yoki ogohlantirish + "sinab ko'ring va natijani yozing" CTA.

Qoidalar: 170-240 so'z, Telegram Markdown, umumiy nazariya YO'Q — faqat qilinadigan ishlar. Oxiriga 3-4 hashtag. Kanal linkini qo'shma."""
        return await self._call_llm(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7, max_tokens=800,
        )

    async def generate_tool_review_post(self, tool: str, style: str = DEFAULT_STYLE) -> str:
        """AI vosita sharhi — halol, amaliy review."""
        style_text = await self._style_text(style)
        prompt = f"""Sen AI vositalarini har kuni ishlatib ko'radigan, halol fikr bildiradigan sharhlovchisan. Telegram kanalga quyidagi vosita haqida SHARH posti yoz.

Vosita: {tool}

{style_text}

Tuzilish:
- Sarlavha: emoji + **vosita nomi** + bir gapda nima qilishi.
- Nima qiladi — 2-3 gap, oddiy tilda, real foydalanish stsenariysi bilan.
- ✅ Kuchli tomonlari — 2-3 punkt (aniq, "yaxshi" degan umumiy so'zsiz).
- ⚠️ Kamchiliklari — 1-2 punkt (halol bo'l: narx, til qo'llab-quvvatlash, cheklovlar).
- Kimga mos: 1 gap. Narxi: bepul/pullik holati (aniq bilmasang "bepul rejasi bor" kabi ehtiyotkor yoz, narx to'qima).
- Yakun: o'z bahong (masalan "10 dan 8") + "siz ishlatib ko'rganmisiz?" CTA.

Qoidalar: 160-220 so'z, Telegram Markdown. Faktlarni to'qima. Oxiriga 3-4 hashtag. Kanal linkini qo'shma."""
        return await self._call_llm(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7, max_tokens=800,
        )

    async def critique_and_improve(self, text: str, post_kind: str = "") -> str:
        """Yozilgan postni professional muharrir ko'zi bilan tanqid qilib qayta yozadi.

        Qoralama → tahrir jarayoni: hook, bitta g'oya, suvsizlik, CTA, format.
        Xato bo'lsa original qaytadi — sifat qatlami hech qachon postni buzmasligi kerak.
        """
        prompt = f"""Sen Telegram kanallari uchun postlarni tahrir qiladigan tajribali bosh muharrirsan. Quyida qoralama post berilgan{' (turi: ' + post_kind + ')' if post_kind else ''}. Uni quyidagi mezonlar bo'yicha ichingda baholab, YAKUNIY tahrirlangan versiyasini yoz:

1. HOOK — birinchi 1-2 gap o'quvchini to'xtatadimi? Zaif bo'lsa kuchaytir.
2. BITTA G'OYA — post bitta aniq fikrga qurilganmi? Chetga chiqishlarni ol.
3. SUV — takror, umumiy gap, keraksiz kirish so'zlarni qisqart. Har gap qiymat bersin.
4. OQIM — gaplar tabiiy ulanadimi, o'zbek tili grammatikasi toza va ravonmi?
5. CTA — yakunda o'quvchini harakatga undaydigan tabiiy chaqiruv bormi?
6. FORMAT — Telegram Markdown (**qalin**, _kursiv_) to'g'ri ishlatilganmi, emoji me'yoridami?

Qat'iy qoidalar:
- Postning mazmuni, faktlari va uslubini (ohangini) SAQLA — sen tahrirchisan, qayta muallif emas.
- Yangi fakt, raqam yoki va'da QO'SHMA.
- Uzunlikni sezilarli oshirma (±15% chegarada qol).
- Hashtaglarni saqla. FAQAT o'zbek LOTIN alifbosida.
- FAQAT yakuniy post matnini qaytar — baho, izoh, sarlavha yozma.

Qoralama post:
{text}"""
        try:
            improved = await self._call_llm(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
                max_tokens=1200,
            )
            improved = improved.strip()
            # Muharrir bo'sh yoki keskin qisqartirilgan natija bersa — originalga qaytamiz
            if not improved or len(improved) < len(text) * 0.5:
                logger.warning("Critique natijasi shubhali qisqa — original qoldirildi")
                return text
            return improved
        except Exception as e:
            logger.warning(f"Critique xatosi — original qoldirildi: {e}")
            return text

    def get_todays_practical_topic(self) -> str:
        from datetime import date
        return PRACTICAL_TOPICS[date.today().toordinal() % len(PRACTICAL_TOPICS)]

    def get_todays_tool(self) -> str:
        from datetime import date
        return AI_TOOLS[date.today().toordinal() % len(AI_TOOLS)]

    def get_todays_topic(self) -> str:
        """Bugungi sanaga qarab ta'limiy mavzu tanlaydi (rotatsiya)."""
        from datetime import date
        day_index = date.today().toordinal() % len(EDUCATIONAL_TOPICS)
        return EDUCATIONAL_TOPICS[day_index]

    def get_different_topic(self, exclude: str) -> str:
        """Berilgan mavzudan farqli, tasodifiy mavzu qaytaradi."""
        import random
        candidates = [t for t in EDUCATIONAL_TOPICS if t != exclude]
        return random.choice(candidates)

    async def get_ai_news_shuffled(self, count: int = 3) -> list[dict]:
        """Yangiliklar ro'yxatini tasodifiy tartibda qaytaradi (boshqa post uchun).

        get_ai_news bilan bir xil birlashgan manbadan (RSS + Telegram) keng
        ro'yxat olib aralashtiradi — curation boshqa yangilikni tanlaydi.
        """
        import random
        unique = await self.get_ai_news(count=40)
        random.shuffle(unique)
        return unique[:count]

    async def generate_weekly_digest(self, posts: list[dict]) -> str:
        """Haftalik dayjest — eng yaxshi postlarning qisqacha mazmuni va linklari."""
        from datetime import date, timedelta
        today = date.today()
        week_start = today - timedelta(days=6)
        date_range = f"{week_start.day}-{today.day} {today.strftime('%B')}"

        items_text = ""
        for i, p in enumerate(posts, 1):
            label = "🎓 Ta'limiy" if p["post_type"] == "educational" else "🌐 Yangilik"
            topic_line = f" ({p['topic']})" if p.get("topic") else ""
            views_line = f" · {p['views']} ko'rish" if p.get("views", 0) > 0 else ""
            link = f"https://t.me/Yetmishboyev_Sh/{p['telegram_message_id']}"
            items_text += (
                f"\n{i}. [{label}{topic_line}] {p['text_preview'][:200]}\n"
                f"   Ko'rishlar: {p.get('views', 0)}{views_line}\n"
                f"   Link: {link}\n"
            )

        prompt = f"""
Sen sun'iy intellekt kanalining PR menedjeri va muharririsisan.
Quyida bu haftadagi kanalga yuborilgan eng yaxshi postlar ro'yxati berilgan.
Ularni asosida haftalik dayjest post tayyorla.

Hafta: {date_range}
Postlar:
{items_text}

Talablar:
- Jonli inson — kanal muharriri — yozgandek, samimiy va qiziqarli ohangda yoz, shablon ko'rinishidan qoch.
- To'g'ri adabiy o'zbek tilida, ravon va qiziqarli yoz.
- Emojilarni matn ichida ham tabiiy tarzda ishlat.
- HTML formatida yoz: sarlavhalar <b>qalin</b>, bo'limlar orasida bo'sh qator.
- Har bir post uchun 1-2 jumlada qisqacha mazmun ber, mumkin bo'lsa o'z sharhingni qo'sh.
- Har bir postning ostiga <a href="[post_link]">📖 Batafsil o'qish →</a> tugmasini qo'y.
- Quyidagi tuzilishda yoz:

📊 <b>Haftalik dayjest — {date_range}</b>

Bu hafta kanalimizda eng ko'p o'qilgan postlar:

1️⃣ <b>[Post mavzusi yoki sarlavhasi]</b>
[Qisqacha 1-2 jumlada mazmun]
<a href="[link]">📖 Batafsil o'qish →</a>

2️⃣ ...

(xuddi shu tarzda davom et)

So'ngida qisqa xulosa: "Bu haftada eng ko'p qiziqish uyg'otgan mavzu: [mavzu nomi]"

Muhim: post linklar to'liq va to'g'ri bo'lishi shart. Oxiriga kanal linkini QO'SHMA — u avtomatik qo'shiladi.
"""
        return await self._call_llm(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
            max_tokens=1200,
        )


news_fetcher = NewsFetcher()
