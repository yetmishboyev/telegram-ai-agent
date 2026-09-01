import httpx
import json
import xml.etree.ElementTree as ET
from loguru import logger

from app.ai.agents.base_agent import BaseAgent
from app.ai.models import ModelTier
from app.ai.schemas import (
    CURATION_SCHEMA, GROWTH_STRATEGY_SCHEMA, POLL_SCHEMA, QUIZ_SCHEMA,
)
from app.utils.uz_text import to_latin_uz


# Sun'iy intellektga oid feed manbalar — nufuzli, mustaqil saytlar.
# Har biri jonli tekshirilgan (2026-09-01: HTTP 200 + o'qiladigan sana);
# ishlamay qolsa fetch_rss jimgina o'tkazadi. Holatini `check_sources()`
# ko'rsatadi — o'lgan feed sezilmay qolmasligi uchun.
AI_NEWS_FEEDS = [
    # ── keng qamrovli AI yangiliklari ──
    "https://news.google.com/rss/search?q=artificial+intelligence+AI&hl=en-US&gl=US&ceid=US:en",
    "https://venturebeat.com/category/ai/feed/",
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.technologyreview.com/feed/",          # MIT Technology Review
    "https://www.wired.com/feed/tag/ai/latest/rss",    # Wired AI
    "https://feeds.arstechnica.com/arstechnica/index", # Ars Technica
    "https://the-decoder.com/feed/",                   # The Decoder (AI-fokus)
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",  # Verge AI (Atom)
    "https://www.marktechpost.com/feed/",              # MarkTechPost (AI-fokus)
    # ── birlamchi manbalar: laboratoriyalarning o'z e'lonlari ──
    "https://openai.com/news/rss.xml",                 # OpenAI
    "https://blog.google/technology/ai/rss/",          # Google AI
    "https://deepmind.google/blog/rss.xml",            # Google DeepMind
    "https://huggingface.co/blog/feed.xml",            # Hugging Face
    # ── saralangan / tahliliy ──
    "https://www.techmeme.com/feed.xml",               # Techmeme — sohaning kun tartibi
    "https://hnrss.org/frontpage?points=200",          # Hacker News (200+ ball)
    "https://simonwillison.net/atom/everything/",      # Simon Willison — amaliy AI (Atom)
    "https://jack-clark.net/feed/",                    # Import AI — haftalik tahlil
]

# Bir vaqtda ochiladigan feed ulanishlari soni. Feedlar ilgari BIRIN-KETIN
# yuklanardi: 15s timeout × 17 feed = eng yomon holatda ~4 daqiqa, ya'ni
# bitta sekin sayt butun post tayyorlashni kechiktirardi.
FEED_CONCURRENCY = 8

# Mashhur AI/texnologiya Telegram kanallari — userbot o'qiydi.
# Kanal ochiq bo'lmasa yoki nomi o'zgargan bo'lsa fetch_telegram_news uni
# o'tkazib yuboradi; qaysi biri ishlayotganini `check_sources()` ko'rsatadi.
NEWS_TG_CHANNELS = [
    "@ai_newz",
    "@seeallochnaya",
    "@data_secrets",
    "@gonzo_ML",
    "@denissexy",
    "@ai_machinelearning_big_data",
]

# Faqat shu oynadagi yangiliklar "so'nggi" hisoblanadi
NEWS_FRESHNESS_HOURS = 48

# Curation'ga beriladigan nomzodlar soni. Ilgari 10 edi — round-robin bitta
# aylanishda tugab, har manbadan FAQAT 1 ta (bosh sahifadagi eng yangi)
# sarlavha olinardi. Natijada tanlov doim bir xil "kun mavzusi" doirasida
# qolib, kanalda bitta tema takrorlanardi.
# Manbalar 12 ta bo'lganda 30 yetardi; hozir ~23 ta (17 feed + 6 kanal), ya'ni
# 30 nomzod yana har manbadan bittaga tushib qolardi. Hovuz manbalar soniga
# nisbatan o'sishi kerak — 50 nomzod har manbadan 2-3 ta beradi.
NEWS_POOL_SIZE = 50

# Tanlangan yangilik oxirgi shu kun ichida chiqqan postlar bilan solishtiriladi
NEWS_HISTORY_DAYS = 14

# Sarlavhalar shu darajadan ko'p o'xshasa — bir xil yangilik deb hisoblanadi
NEWS_DUPLICATE_RATIO = 0.75

# ─── yangilik kategoriyalari ───────────────────────────────────────────────────
# Curation'ning yagona mezoni "eng qiziqarli muhokama uyg'otadigani" edi —
# model bunga har kuni bir xil javob berardi: eng shov-shuvli, "AI nazoratdan
# chiqdi" turidagi sarlavha. Kategoriya katalogi + oxirgi postlar tarixi
# promptga berilib, tanlov aylanishi ta'minlanadi.
NEWS_CATEGORIES: dict[str, str] = {
    "mahsulot":   "yangi model, mahsulot yoki funksiya chiqishi",
    "tadqiqot":   "ilmiy natija, benchmark, texnik kashfiyot",
    "biznes":     "investitsiya, bozor, kompaniya strategiyasi, mehnat bozori",
    "vosita":     "foydalanuvchi bugun ishlata oladigan amaliy vosita yoki servis",
    "siyosat":    "regulyatsiya, qonun, davlat siyosati, sud ishi",
    "xavfsizlik": "AI xavfi, nazorat, zaiflik, etika muammosi",
}
NEWS_FALLBACK_CATEGORY = "boshqa"

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


# ─── ta'limiy post janrlari ────────────────────────────────────────────────────
# Mavzu har kuni aylanardi, lekin post SHAKLI o'zgarmasdi ("X nima → qanday
# ishlaydi → qayerda qo'llaniladi → nega muhim"), shuning uchun barcha ta'limiy
# postlar bir xil o'qilardi. Janr mavzudan MUSTAQIL aylanadi: 6 janr va 25 mavzu
# o'zaro tub, shuning uchun mavzu-janr juftligi 150 postda bir marta takrorlanadi.
EDUCATIONAL_FORMATS: dict[str, str] = {
    "tarif": (
        "JANR — TUSHUNTIRISH: tushunchani noldan tushuntir.\n"
        "- Kundalik hayotdan tanish misol bilan boshla, keyin atamaga o't\n"
        "- Qanday ishlashini qisqa, o'xshatish bilan ayt\n"
        "- 2-3 ta real qo'llanish holati\n"
        "- Nega bu bilim o'quvchiga kerakligi bilan yakunla"
    ),
    "xato": (
        "JANR — KENG TARQALGAN XATOLAR: shu mavzuda odamlar qiladigan xatolar.\n"
        "- Boshida: bu mavzuda ko'pchilik nimani noto'g'ri qiladi\n"
        "- 3 ta aniq xato — har birida: xato nima, nega yuz beradi, TO'G'RI yo'l\n"
        "- Yakunda: shu 3 tasidan qutulgan odam nimaga erishadi"
    ),
    "taqqoslash": (
        "JANR — TAQQOSLASH: mavzuni u bilan doim aralashtiriladigan yaqin "
        "tushuncha bilan yonma-yon qo'y.\n"
        "- Boshida: nega bu ikkisi aralashtiriladi\n"
        "- Asosiy farqni 1 gapda ayt, keyin 3 o'lchov bo'yicha yonma-yon ko'rsat\n"
        "- Qaysi holatda qaysi biri kerakligi bilan yakunla"
    ),
    "mif": (
        "JANR — MIFLARNI BUZISH: mavzu atrofidagi noto'g'ri qarashlar.\n"
        "- Boshida: bu haqda eng ko'p uchraydigan noto'g'ri fikr\n"
        "- 3 ta mif — har birida: «Mif: ...» keyin «Haqiqat: ...»\n"
        "- Yakunda: haqiqatni bilgan odam nimani boshqacha qiladi"
    ),
    "keys": (
        "JANR — REAL HOLAT: mavzuni bitta konkret hikoya orqali tushuntir.\n"
        "- Aniq (lekin o'ylab topilmagan, tipik) holat bilan boshla: kim, "
        "qanday muammoga duch keldi\n"
        "- Mavzudagi tushuncha bu muammoni qanday hal qilgani\n"
        "- Natija, keyin o'quvchi shu holatdan oladigan 2 ta xulosa\n"
        "- Aniq raqam yoki kompaniya nomini TO'QIMA — umumlashtirib yoz"
    ),
    "analogiya": (
        "JANR — O'XSHATISH: butun postni bitta kuchli o'xshatish ustiga qur.\n"
        "- Mavzuni AI'ga aloqasi yo'q, hammaga tanish narsaga o'xshat "
        "(oshxona, yo'l harakati, kutubxona, bozor kabi)\n"
        "- O'xshatishni 3 nuqtada davom ettir: nima nimaga to'g'ri keladi\n"
        "- O'xshatish QAYERDA buzilishini ham ayt (halollik uchun)\n"
        "- Asl atama bilan bir gaplik xulosa"
    ),
}

# Amaliy qo'llanma postlari uchun mavzular (rotatsiya) — eng ulashiladigan format.
# Ro'yxat 12 ta edi, amaliy post esa haftada 2 marta chiqadi — katalog 6 haftada
# tugab, "CV yozish" kabi mavzular oyiga qayta chiqardi. Bundan tashqari mavzular
# bir gala edi ("ChatGPT bilan MATN yozish"), shuning uchun turli mavzular ham
# bir xil o'qilardi. Ro'yxat kengaytirildi va matn yozishdan tashqari sohalar
# (tahlil, o'rganish, tartib, media, xavfsizlik) qo'shildi.
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
    "ChatGPT bilan ish intervyusiga tayyorgarlik ko'rish",
    "AI javoblarini fakt-tekshiruvdan o'tkazish",
    "ChatGPT'ni o'zingizga sozlash (Custom Instructions)",
    "AI bilan kitob va uzun maqolani konspekt qilish",
    "AI yordamida rasm generatsiya qilish — prompt asoslari",
    "Ovozli xabar va uchrashuvni AI bilan matnga aylantirish",
    "AI bilan kun tartibi va haftalik reja tuzish",
    "AI yordamida kod yozishni noldan boshlash",
    "AI bilan ma'lumotlarni jadvalga solib tahlil qilish",
    "AI bilan sayohat va byudjet rejasini tuzish",
    "AI chatiga kontekst berish san'ati — javob sifatini oshirish",
    "AI'ga shaxsiy ma'lumot bermaslik — xavfsizlik qoidalari",
]

# ─── amaliy post janrlari ──────────────────────────────────────────────────────
# Ta'limiy postlarda janr rotatsiyasi bor edi, amaliyda esa yo'q — shuning uchun
# har seshanba va juma posti bir xil skelet bilan chiqardi ("sarlavha → kirish →
# 3-5 qadam → pro-maslahat"). Janr mavzudan mustaqil aylanadi.
PRACTICAL_FORMATS: dict[str, str] = {
    "qadam": (
        "JANR — QADAMMA-QADAM: o'quvchi ketma-ket bajaradigan yo'riqnoma.\n"
        "- Kirish 1-2 gap: bu nima uchun kerak, qanday muammoni yechadi\n"
        "- 3-5 ta RAQAMLANGAN qadam, har birida aniq amal\n"
        "- Kerak joyda aynan yoziladigan prompt namunasini _kursiv_ da ber\n"
        "- Yakunda kichik pro-maslahat yoki ogohlantirish"
    ),
    "prompt": (
        "JANR — TAYYOR PROMPT: postning markazida ko'chirib ishlatiladigan "
        "bitta prompt shabloni tursin.\n"
        "- Boshida: bu prompt qanday natija beradi\n"
        "- To'liq prompt shabloni — o'quvchi to'ldiradigan joylari [kvadrat "
        "qavs] bilan belgilangan holda\n"
        "- Shablonning har bo'lagi nega kerakligini 3 punktda tushuntir\n"
        "- 1 ta o'zgartirish bilan uni boshqa vazifaga moslash yo'li"
    ),
    "oldin_keyin": (
        "JANR — YOMON PROMPT / YAXSHI PROMPT: farqni yonma-yon ko'rsat.\n"
        "- Odatda yoziladigan zaif so'rov namunasi va u qanday javob berishi\n"
        "- O'sha so'rovning kuchaytirilgan varianti — nima qo'shilgani "
        "aniq ko'rinsin\n"
        "- Nima o'zgargani va nega ishlagani 3 punktda\n"
        "- O'quvchi shu qoidani o'z vazifasiga qanday ko'chirishi"
    ),
    "xato": (
        "JANR — XATOLAR USTIDA: shu ishni qilayotganda ko'pchilik nimani "
        "noto'g'ri qiladi.\n"
        "- Boshida: shu vazifada eng ko'p uchraydigan muammo\n"
        "- 3 ta aniq xato — har birida: xato nima, nega natijani buzadi, "
        "o'rniga NIMA qilish kerak\n"
        "- Yakunda: shu 3 tasidan qutulgan odam qanday natija oladi"
    ),
    "checklist": (
        "JANR — TEKSHIRUV RO'YXATI: o'quvchi ish oldidan yoki ishdan keyin "
        "yurib chiqadigan ro'yxat.\n"
        "- Boshida 1-2 gap: bu ro'yxat qaysi paytda ishlatiladi\n"
        "- 5-7 ta qisqa, ✅ bilan belgilangan tekshiruv nuqtasi — har biri "
        "«ha/yo'q» deb javob beriladigan darajada aniq\n"
        "- Yakunda: ro'yxatdan qaysi biri eng ko'p o'tkazib yuborilishi"
    ),
}

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

# ─── o'zbek tili sifat qatlami ────────────────────────────────────────────────
# Postlar grammatik jihatdan to'g'ri edi, lekin O'QISH og'ir edi: model ingliz
# atamasini tarjimasiz tashlab ketardi ("inference", "fine-tuning"), gaplar
# ergash gaplar bilan cho'zilardi va bir atama uch postda uch xil yozilardi.
# Bu qoida barcha generatorlarga qo'shiladi.
_UZBEK_RULE = (
    "\n\nO'ZBEK TILI — O'QUVCHI TUSHUNISHI UCHUN:\n"
    "- Har bir chet atamani BIRINCHI marta ishlatganda qavs ichida oching: "
    "«inference (modelning javob berish bosqichi)». Keyingi safar qavs shart emas.\n"
    "- Gaplar QISQA bo'lsin — bittasida bitta fikr. 20 so'zdan uzun gapni ikkiga bo'l.\n"
    "- Ruscha yoki inglizcha gap qurilishini ko'chirma. «amalga oshirish», "
    "«hisoblanadi», «tashkil etadi» kabi rasmiy shtamplar o'rniga tirik fe'l ishlat: "
    "«qiladi», «bo'ladi», «beradi».\n"
    "- Kitobiy so'z o'rniga kundalik so'zni tanla: «mazkur» emas «shu», "
    "«ushbu» emas «bu», «shuningdek» emas «yana».\n"
    "- Atamalarni QUYIDAGI lug'atdagidek yoz — bir atama har postda bir xil "
    "atalsin, aks holda o'quvchi ularni boshqa-boshqa narsa deb o'ylaydi:\n"
    + "\n".join(f"  · {en} — {uz}" for en, uz in [
        ("Large Language Model (LLM)", "katta til modeli"),
        ("prompt", "so'rov (prompt)"),
        ("training", "o'qitish"),
        ("inference", "javob berish bosqichi"),
        ("fine-tuning", "modelni moslashtirish (fine-tuning)"),
        ("token", "token (so'z bo'lagi)"),
        ("embedding", "vektor tasvir (embedding)"),
        ("hallucination", "to'qib chiqarish (hallucination)"),
        ("dataset", "ma'lumotlar to'plami"),
        ("open-source", "ochiq kodli"),
        ("benchmark", "sinov o'lchovi (benchmark)"),
        ("context window", "kontekst oynasi"),
        ("agent", "agent (o'zi harakat qiladigan dastur)"),
    ])
    + "\n- Yoz-da tekshir: notanish odam bu postni bir marta o'qib tushunadimi? "
      "Tushunmasa — atamani soddalashtir yoki misol qo'sh."
)

# Postning asosiy joyi ko'zga tashlanib turishi uchun — `> ` qatorlari
# `channel_poster._md_to_html` da Telegram sitata blokiga aylanadi.
_QUOTE_RULE = (
    "- SITATA: postning eng muhim fikrini ALOHIDA qatorda `> ` belgisi bilan "
    "boshlab yoz — Telegram uni sitata bloki qilib ajratib ko'rsatadi. Postda "
    "1 ta (uzun postda ko'pi bilan 2 ta) sitata bo'lsin, har biri 1-2 qisqa gap. "
    "Sitata mustaqil o'qilsin, yonidagi matnni so'zma-so'z takrorlamasin va "
    "sarlavha yoki hashtag qatori bo'lmasin."
)


def style_instruction(style: str) -> str:
    return POST_STYLES.get(style, POST_STYLES[DEFAULT_STYLE])["instruction"] + _LATIN_RULE


class NewsFetcher(BaseAgent):

    tier = ModelTier.DEEP

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
        Shuningdek markdown-escape qilingan hashtag (\\#) va sitata (\\>)
        belgilarini, hamda (uslub o'rganilgan bo'lsa) namuna kanalning imzosini
        deterministik tozalaydi — prompt qoidasi yetarli emas, model manba nomini
        ko'rib postga qo'shib yuborishi mumkin."""
        import re as _re
        result = await super()._call_llm(*args, **kwargs)
        result = to_latin_uz(result).replace("\\#", "#").replace("\\>", ">")
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
        """pubDate so'nggi `hours` ichidami.

        Sanasi yo'q yoki o'qilmaydigan item TASHLANADI: barcha 9 feed har bir
        item uchun pubDate beradi (2026-08 da tekshirilgan), shuning uchun sana
        yo'qligi feed buzilganini bildiradi — bunday itemni ichga qo'yish
        eskirgan yangilikning qayta-qayta tanlanishiga olib kelardi.
        """
        if not pub_date:
            return False
        from datetime import datetime, timezone, timedelta
        dt = NewsFetcher._parse_feed_date(pub_date)
        if dt is None:
            logger.warning(f"Sana o'qilmadi, item o'tkazib yuborildi: {pub_date[:40]!r}")
            return False
        return dt >= datetime.now(timezone.utc) - timedelta(hours=hours)

    @staticmethod
    def _parse_feed_date(raw: str):
        """Feed sanasini o'qiydi: RSS (RFC 822) ham, Atom (ISO 8601) ham.

        Atom feedlar `2026-09-01T09:00:00Z` beradi — `parsedate_to_datetime`
        buni o'qiy olmaydi, shuning uchun ikkinchi urinish ISO bilan bo'ladi.
        """
        from datetime import datetime, timezone
        raw = (raw or "").strip()
        if not raw:
            return None
        dt = None
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(raw)
        except Exception:
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except Exception:
                return None
        if dt is None:
            return None
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt

    async def fetch_rss(self, url: str, limit: int = 5) -> list[dict]:
        """RSS feeddan yangiliklar oladi (faqat so'nggi NEWS_FRESHNESS_HOURS ichidagilar)."""
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()
        except Exception as e:
            logger.warning(f"RSS fetch xatosi ({url}): {e}")
            return []

        import re
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.removeprefix("www.").removeprefix("feeds.")

        items: list[dict] = []
        try:
            root = ET.fromstring(resp.text)
            for title, link, desc, pub in self._feed_entries(root):
                if len(items) >= limit:
                    break
                desc = re.sub(r"<[^>]+>", "", desc)[:300]
                if title and self._is_fresh(pub):
                    items.append({"title": title, "link": link, "desc": desc, "source": domain})
        except Exception as e:
            logger.warning(f"RSS parse xatosi ({domain}): {e}")

        return items

    @staticmethod
    def _feed_entries(root) -> list[tuple[str, str, str, str]]:
        """Feed elementlarini `(title, link, desc, date)` ko'rinishida beradi.

        Ikki format qo'llab-quvvatlanadi. Ilgari faqat RSS (`<channel><item>`)
        o'qilardi — Atom feed (`<feed><entry>`) `channel` topilmagani uchun
        JIMGINA bo'sh ro'yxat qaytarardi, ya'ni manba ulangandek ko'rinib,
        aslida hech narsa bermasdi.
        """
        ns = "{http://www.w3.org/2005/Atom}"
        channel = root.find("channel")
        if channel is not None:                       # RSS 2.0
            return [
                (
                    (i.findtext("title") or "").strip(),
                    (i.findtext("link") or "").strip(),
                    (i.findtext("description") or "").strip(),
                    (i.findtext("pubDate") or "").strip(),
                )
                for i in channel.findall("item")
            ]

        entries = root.findall(f"{ns}entry")          # Atom
        out: list[tuple[str, str, str, str]] = []
        for e in entries:
            link_el = e.find(f"{ns}link")
            link = (link_el.get("href") if link_el is not None else "") or ""
            desc = (e.findtext(f"{ns}summary") or e.findtext(f"{ns}content") or "").strip()
            pub = (e.findtext(f"{ns}published") or e.findtext(f"{ns}updated") or "").strip()
            out.append(((e.findtext(f"{ns}title") or "").strip(), link, desc, pub))
        return out

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

    async def check_sources(self) -> dict:
        """Har bir manbani jonli tekshiradi: nechta va qanchalik yangi xabar beradi.

        Manba o'lganda tizim jimgina ishlashda davom etardi — feed 404 qaytarsa
        ham, Atom formatiga o'tsa ham natija shunchaki bo'sh ro'yxat edi.
        Bu tekshiruv nima ishlayotganini ko'rsatadi.

        Returns: `{"feeds": [...], "channels": [...]}` — har biri
        `{"name", "ok", "fresh", "note"}`.
        """
        import asyncio
        sem = asyncio.Semaphore(FEED_CONCURRENCY)

        async def probe(url: str) -> dict:
            from urllib.parse import urlparse
            name = urlparse(url).netloc.removeprefix("www.").removeprefix("feeds.")
            try:
                async with sem:
                    items = await self.fetch_rss(url, limit=50)
            except Exception as e:
                return {"name": name, "ok": False, "fresh": 0, "note": str(e)[:60]}
            # Bo'sh natija ikki xil bo'ladi: manba jim (normal) yoki buzilgan.
            # Farqini ajratish uchun xom javobni ham ko'ramiz.
            return {
                "name": name, "ok": True, "fresh": len(items),
                "note": "" if items else f"{NEWS_FRESHNESS_HOURS} soatda yangilik yo'q",
            }

        feeds = list(await asyncio.gather(*(probe(u) for u in AI_NEWS_FEEDS)))

        channels: list[dict] = []
        from app.services.telegram_service import telegram_service
        for ch in NEWS_TG_CHANNELS:
            try:
                msgs = await telegram_service._client.get_messages(ch, limit=5)
                channels.append({
                    "name": ch, "ok": True, "fresh": len([m for m in msgs if m]),
                    "note": "",
                })
            except Exception as e:
                channels.append({"name": ch, "ok": False, "fresh": 0, "note": str(e)[:60]})

        dead = [f["name"] for f in feeds + channels if not f["ok"]]
        if dead:
            logger.warning(f"Ishlamayotgan manbalar: {', '.join(dead)}")
        return {"feeds": feeds, "channels": channels}

    async def get_ai_news(self, count: int = 3) -> list[dict]:
        """RSS saytlar + Telegram kanallardan yangilik yig'ib, dedup qilib qaytaradi."""
        import asyncio
        sem = asyncio.Semaphore(FEED_CONCURRENCY)

        async def one(url: str) -> list[dict]:
            async with sem:
                return await self.fetch_rss(url, limit=6)

        # Feedlar parallel yuklanadi — sekin manba boshqalarni kutkazmaydi.
        # return_exceptions: bitta feed yiqilsa qolganlari baribir keladi.
        results = await asyncio.gather(
            *(one(u) for u in AI_NEWS_FEEDS), return_exceptions=True
        )
        all_items: list[dict] = []
        for url, res in zip(AI_NEWS_FEEDS, results):
            if isinstance(res, BaseException):
                logger.warning(f"Feed o'qilmadi ({url}): {res}")
                continue
            all_items.extend(res)

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

    async def generate_educational_post(
        self, topic: str, style: str = DEFAULT_STYLE, fmt: str | None = None
    ) -> str:
        """Berilgan AI mavzuda o'zbek tilida ta'limiy post yaratadi.

        `fmt` — `EDUCATIONAL_FORMATS` kaliti (janr). Berilmasa bugungi
        rotatsiyadan olinadi, shunda ketma-ket postlar bir xil shaklda chiqmaydi.
        """
        style_text = await self._style_text(style)
        fmt = fmt if fmt in EDUCATIONAL_FORMATS else self.get_todays_educational_format()
        format_text = EDUCATIONAL_FORMATS[fmt]
        prompt = f"""
Sen sun'iy intellekt sohasida 8-10 yillik tajribaga ega, o'z auditoriyasiga ega bo'lgan ekspert-muallifsan. Telegram kanalingga quyidagi mavzu bo'yicha post yozasan.

Mavzu: {topic}

{format_text}

{style_text}

Talablar:
- Jonli inson yozgandek yoz — quruq, shablon yoki "AI yozgan" his qildiradigan ohangdan qoch. Xuddi tanishingga tushuntirayotgandek, samimiy va qiziqarli tarzda yoz.
- O'zingning fikringni, kichik bir kuzatuvingni yoki qiziqarli detalni qo'sh — faqat quruq ta'rif bo'lmasin.
- To'g'ri adabiy o'zbek tilida yoz. Grammatika xatolariga yo'l qo'yma.
- So'zlarni to'g'ri qo'lla: "sun'iy intellekt" (suniy emas), "dastur" (dasturiy ta'minot), "foydalanuvchi" va hokazo.
- Emojilarni faqat sarlavhalarda emas, matn ichida ham joyida va tabiiy ishlat (haddan tashqari ko'p bo'lmasin — mazmunni jonlantirish uchun).
- Telegram Markdown formatida yoz (**qalin**, _kursiv_).
- Hajmi: 220–280 so'z.
- Emoji + **qalin sarlavha** qatori bilan boshla. Sarlavha janrga mos bo'lsin va oldingi postlarni eslatmasin — "🎓" ni har safar takrorlashga majbur emassan.
- Yuqoridagi janr tuzilishiga amal qil, lekin bo'lim sarlavhalarini so'zma-so'z yozma — matn tabiiy oqsin.
{_QUOTE_RULE}{_UZBEK_RULE}
- Oxiriga MAVZUGA mos 3-4 ta hashtag yoz (aynan shu mavzudan kelib chiqib — har postda bir xil to'plamni takrorlama). Kanal linkini qo'shma.
"""
        return await self._call_llm(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,  # janr xilma-xilligi uchun biroz yuqori
            max_tokens=700,
        )

    @staticmethod
    def _norm_title(title: str) -> str:
        """Sarlavhani solishtirish uchun normallashtiradi (registr, tinish belgilari)."""
        import re as _re
        cleaned = _re.sub(r"[^\w\s]", " ", (title or "").lower())
        return " ".join(cleaned.split())

    @classmethod
    def _is_repeat(cls, title: str, previous_titles: list[str]) -> bool:
        """Sarlavha oldin chiqqan postlardan birini takrorlaydimi."""
        from difflib import SequenceMatcher
        current = cls._norm_title(title)
        if not current:
            return False
        for prev in previous_titles:
            other = cls._norm_title(prev)
            if not other:
                continue
            if current == other or \
                    SequenceMatcher(None, current, other).ratio() >= NEWS_DUPLICATE_RATIO:
                return True
        return False

    async def curate_top_news(
        self, items: list[dict], recent: list[dict] | None = None
    ) -> dict | None:
        """Yangiliklar ichidan kanal uchun ENG qimmatlisini tanlaydi (curation).

        `recent` — oxirgi kunlarda chiqqan yangilik postlari (eng yangisi
        birinchi): `[{"topic": ..., "category": ...}]`. Ikki qatlamda ishlaydi:
          1. Deterministik — sarlavhasi oldingi post bilan mos keladigan
             nomzodlar ro'yxatdan olib tashlanadi.
          2. Prompt — tarix va kategoriya katalogi modelga berilib, ketma-ket
             bir xil temaga tushib qolmasligi talab qilinadi.

        Returns: {item, reason, category} yoki None (ro'yxat bo'sh bo'lsa).
        """
        from app.ai.agents.json_parse import parse_json_response

        if not items:
            return None

        recent = recent or []
        previous_titles = [str(r.get("topic") or "") for r in recent]

        # 1-qatlam: allaqachon chiqqan yangilikni nomzodlardan olib tashlaymiz
        fresh = [it for it in items if not self._is_repeat(it.get("title", ""), previous_titles)]
        if not fresh:
            # Hammasi takror bo'lsa ham kanal postsiz qolmasligi kerak
            logger.warning("Barcha nomzodlar oldingi postlarni takrorlaydi — filtrsiz tanlanadi")
            fresh = items
        elif len(fresh) < len(items):
            logger.info(f"Takroriy yangilik olib tashlandi: {len(items) - len(fresh)} ta")
        items = fresh

        if len(items) == 1:
            return {"item": items[0], "reason": "", "category": NEWS_FALLBACK_CATEGORY}

        listing = "\n".join(
            f"{i}. [{it.get('source', 'sayt')}] {it['title']}\n   {it.get('desc', '')[:150]}"
            for i, it in enumerate(items)
        )
        catalog = "\n".join(f"- {key}: {desc}" for key, desc in NEWS_CATEGORIES.items())

        if recent:
            history_lines = "\n".join(
                f"- [{r.get('category') or '?'}] {str(r.get('topic') or '')[:120]}"
                for r in recent[:10]
            )
            last_cats = [str(r.get("category")) for r in recent[:2] if r.get("category")]
            avoid_line = (
                f"\n\nOxirgi postlar kategoriyasi: {', '.join(last_cats)}. "
                f"BOSHQA kategoriyadagi yangilikni tanla."
                if last_cats else ""
            )
            history_block = (
                f"Oxirgi {NEWS_HISTORY_DAYS} kunda kanalda chiqqan yangilik postlari "
                f"(eng yangisi birinchi):\n{history_lines}{avoid_line}"
            )
        else:
            history_block = "Kanalda hali yangilik posti chiqmagan."

        prompt = f"""Sen sun'iy intellekt mavzusidagi o'zbek Telegram kanalining bosh muharririsan. Auditoriya: texnologiyaga qiziquvchi oddiy foydalanuvchilar va IT mutaxassislar.

{history_block}

Kategoriyalar:
{catalog}

Quyidagi yangiliklar ichidan kanal uchun BITTASINI tanla:

{listing}

Tanlash qoidalari:
1. TAKRORLAMA — yuqorida chiqqan yangilikning o'zini yoki juda yaqin variantini tanlama.
2. XILMA-XILLIK — oxirgi postlar bilan bir xil kategoriyaga tushmasin. Kanal faqat bitta tema (masalan AI xavfsizligi) haqida bo'lib qolmasligi kerak.
3. QIYMAT — shov-shuvli sarlavha emas, auditoriya uchun haqiqiy foydali yoki muhim yangilikni tanla. Dramatik ("AI nazoratdan chiqdi" turidagi) sarlavha faqat haqiqatan muhim bo'lsa tanlanadi.
4. Tanlagan yangilikning kategoriyasini yuqoridagi ro'yxatdan belgila.

Faqat JSON qaytar: {{"index": <raqam>, "category": "<kategoriya kaliti>", "reason": "nega aynan shu — 1 gap"}}"""

        try:
            raw = await self._call_llm(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3, max_tokens=300,
                response_schema=CURATION_SCHEMA,
            )
            data = parse_json_response(raw)
            idx = int(data.get("index", 0))
            if not (0 <= idx < len(items)):
                idx = 0
            category = str(data.get("category", "")).strip().lower()
            if category not in NEWS_CATEGORIES:
                category = NEWS_FALLBACK_CATEGORY
            return {
                "item": items[idx],
                "reason": str(data.get("reason", ""))[:200],
                "category": category,
            }
        except Exception as e:
            logger.warning(f"Curation xatosi — birinchi yangilik olinadi: {e}")
            return {"item": items[0], "reason": "", "category": NEWS_FALLBACK_CATEGORY}

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
{_QUOTE_RULE}{_UZBEK_RULE}
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
                response_schema=GROWTH_STRATEGY_SCHEMA,
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
                response_schema=QUIZ_SCHEMA if kind == "quiz" else POLL_SCHEMA,
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
{_QUOTE_RULE}{_UZBEK_RULE}
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
Sitata qatorlarini (`> ` bilan boshlanadigan) saqla — ega boshqacha aytmagan bo'lsa.
Oxiridagi "—", "🔗 Manba: ..." va kanal link qatorlarini OLIB TASHLASH kerak — ular keyinchalik avtomatik qo'shiladi.
"""
        return await self._call_llm(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
            max_tokens=800,
        )

    async def generate_practical_post(
        self, topic: str, style: str = DEFAULT_STYLE, fmt: str | None = None
    ) -> str:
        """Amaliy qo'llanma posti — o'quvchi darhol qo'llay oladigan qadamlar.

        `fmt` — `PRACTICAL_FORMATS` kaliti (janr). Berilmasa bugungi
        rotatsiyadan olinadi, shunda ketma-ket amaliy postlar bir xil
        skelet bilan chiqmaydi.
        """
        style_text = await self._style_text(style)
        fmt = fmt if fmt in PRACTICAL_FORMATS else self.get_todays_practical_format()
        format_text = PRACTICAL_FORMATS[fmt]
        prompt = f"""Sen AI vositalaridan kundalik ishda foydalanadigan amaliyotchi mutaxassissan. Telegram kanalga quyidagi mavzuda AMALIY post yoz — o'quvchi o'qib bo'lgach darhol o'zi qilib ko'ra olsin.

Mavzu: {topic}

{format_text}

{style_text}

Umumiy tuzilish:
- Sarlavha: emoji + **qalin**, aniq natijani va'da qilsin. Janrga mos bo'lsin va oldingi postlarni eslatmasin.
- Yuqoridagi janr tuzilishiga amal qil, lekin bo'lim sarlavhalarini so'zma-so'z yozma — matn tabiiy oqsin.
- Yakunda o'quvchini harakatga undaydigan tabiiy CTA.

Qoidalar: 170-240 so'z, Telegram Markdown, umumiy nazariya YO'Q — o'quvchi takrorlay oladigan ANIQ QADAMLAR va amallar bo'lsin.
{_QUOTE_RULE}{_UZBEK_RULE}
Oxiriga MAVZUGA mos 3-4 hashtag (har postda bir xil to'plamni takrorlama). Kanal linkini qo'shma."""
        return await self._call_llm(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,  # janr xilma-xilligi uchun biroz yuqori
            max_tokens=800,
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

Qoidalar: 160-220 so'z, Telegram Markdown. Faktlarni to'qima.
{_QUOTE_RULE}{_UZBEK_RULE}
Oxiriga 3-4 hashtag. Kanal linkini qo'shma."""
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
4b. TUSHUNARLILIK — chet atama qavs ichida ochilganmi? 20 so'zdan uzun gap bormi (bo'lib tashla)? «amalga oshirish», «hisoblanadi», «mazkur», «ushbu» kabi rasmiy shtamplar tirik so'zga almashtirilganmi? Bir atama post ichida bir xil atalganmi?
5. CTA — yakunda o'quvchini harakatga undaydigan tabiiy chaqiruv bormi?
6. FORMAT — Telegram Markdown (**qalin**, _kursiv_) to'g'ri ishlatilganmi, emoji me'yoridami?

Qat'iy qoidalar:
- Postning mazmuni, faktlari va uslubini (ohangini) SAQLA — sen tahrirchisan, qayta muallif emas.
- Yangi fakt, raqam yoki va'da QO'SHMA.
- Uzunlikni sezilarli oshirma (±15% chegarada qol).
- SITATA: `> ` bilan boshlangan qatorlarni SAQLA (kerak bo'lsa ichidagi gapni kuchaytir, lekin `> ` belgisini olib tashlama). Agar qoralamada sitata bo'lmasa, eng muhim BITTA fikrni alohida qatorga chiqarib `> ` bilan belgila.
- Hashtaglarni saqla. FAQAT o'zbek LOTIN alifbosida.
- Atamani soddalashtirganda MA'NOSINI buzma — tushunarlilik aniqlik hisobiga bo'lmasin.
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

    @staticmethod
    def _pick_fresh(items: list[str], recent: list[str] | None = None) -> str:
        """Rotatsiyadan yaqinda chiqmagan birinchi mavzuni tanlaydi.

        Sana bo'yicha `ordinal % len(items)` rotatsiyasi o'zicha yetarli emas
        edi: kanalda haftada 2-5 post chiqadi, shuning uchun katalog bir necha
        haftada tugab, mavzular tarix bilan solishtirilmasdan qaytadan
        boshlanardi (o'quvchiga "bir xil postlar" shu yerdan ko'rinardi).

        `recent` — o'sha turdagi oxirgi postlar mavzusi, ENG YANGISI BIRINCHI.
        Rotatsiya tartibi saqlanadi, faqat yaqinda chiqqanlari o'tkazib
        yuboriladi. Hammasi chiqib bo'lgan bo'lsa — eng uzoq vaqt oldin
        chiqqani olinadi. `recent` bo'sh bo'lsa eski xatti-harakat qoladi.
        """
        from datetime import date
        start = date.today().toordinal() % len(items)
        order = [items[(start + i) % len(items)] for i in range(len(items))]
        if not recent:
            return order[0]
        used = {t for t in recent if t}
        for topic in order:
            if topic not in used:
                return topic
        return max(order, key=lambda t: recent.index(t) if t in recent else len(recent))

    def get_todays_practical_topic(self, recent: list[str] | None = None) -> str:
        return self._pick_fresh(PRACTICAL_TOPICS, recent)

    def get_todays_tool(self, recent: list[str] | None = None) -> str:
        return self._pick_fresh(AI_TOOLS, recent)

    def get_todays_topic(self, recent: list[str] | None = None) -> str:
        """Bugungi sanaga qarab ta'limiy mavzu tanlaydi (rotatsiya + tarix)."""
        return self._pick_fresh(EDUCATIONAL_TOPICS, recent)

    def get_todays_educational_format(self) -> str:
        """Bugungi ta'limiy post janrini tanlaydi (mavzudan mustaqil rotatsiya)."""
        from datetime import date
        keys = list(EDUCATIONAL_FORMATS)
        return keys[date.today().toordinal() % len(keys)]

    def get_todays_practical_format(self) -> str:
        """Bugungi amaliy post janrini tanlaydi (mavzudan mustaqil rotatsiya)."""
        from datetime import date
        keys = list(PRACTICAL_FORMATS)
        return keys[date.today().toordinal() % len(keys)]

    async def get_ai_news_shuffled(self, count: int = 3) -> list[dict]:
        """Yangiliklar ro'yxatini tasodifiy tartibda qaytaradi (boshqa post uchun).

        get_ai_news bilan bir xil birlashgan manbadan (RSS + Telegram) keng
        ro'yxat olib aralashtiradi — curation boshqa yangilikni tanlaydi.
        """
        import random
        unique = await self.get_ai_news(count=max(40, count))
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

So'ngida qisqa xulosani SITATA blokiga ol (dayjest HTML formatida, shuning uchun aynan shu tegdan foydalan):
<blockquote>Bu haftada eng ko'p qiziqish uyg'otgan mavzu: [mavzu nomi]</blockquote>

Muhim: post linklar to'liq va to'g'ri bo'lishi shart. Oxiriga kanal linkini QO'SHMA — u avtomatik qo'shiladi.
"""
        return await self._call_llm(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
            max_tokens=1200,
        )


news_fetcher = NewsFetcher()
