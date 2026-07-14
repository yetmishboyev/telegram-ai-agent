import httpx
import xml.etree.ElementTree as ET
from loguru import logger

from app.ai.agents.base_agent import BaseAgent
from app.utils.uz_text import to_latin_uz


# Sun'iy intellektga oid RSS manbalar — turli xil, mustaqil manbalar (bir xil
# Google News so'rovlarining takrorlanishidan qochish uchun; roadmap Faza 3, band 10)
AI_NEWS_FEEDS = [
    "https://news.google.com/rss/search?q=artificial+intelligence+AI&hl=en-US&gl=US&ceid=US:en",
    "https://venturebeat.com/category/ai/feed/",
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://huggingface.co/blog/feed.xml",
]

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


# Post uslublari — ega har post uchun tanlaydi (bot menyusidan).
POST_STYLES: dict[str, dict] = {
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
DEFAULT_STYLE = "chapani"


_LATIN_RULE = (
    " MUHIM: butun matnni FAQAT o'zbek LOTIN alifbosida yoz — biror so'zga ham "
    "krill harflarini (а, б, в, г, д...) aralashtirma."
)


def style_instruction(style: str) -> str:
    return POST_STYLES.get(style, POST_STYLES[DEFAULT_STYLE])["instruction"] + _LATIN_RULE


class NewsFetcher(BaseAgent):

    async def run(self, *args, **kwargs):
        pass

    async def _call_llm(self, *args, **kwargs) -> str:
        """Post generatsiyasi natijasini lotinlashtiradi — LLM ba'zan lotincha
        o'zbek matniga krill harflarni aralashtirib yuboradi (masalan "qidirади")."""
        result = await super()._call_llm(*args, **kwargs)
        return to_latin_uz(result)

    async def fetch_rss(self, url: str, limit: int = 5) -> list[dict]:
        """RSS feeddan yangiliklar oladi."""
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
            for item in channel.findall("item")[:limit]:
                title = item.findtext("title", "").strip()
                link = item.findtext("link", "").strip()
                desc = item.findtext("description", "").strip()
                # HTML teglarini tozalash
                import re
                desc = re.sub(r"<[^>]+>", "", desc)[:300]
                if title:
                    items.append({"title": title, "link": link, "desc": desc})
        except Exception as e:
            logger.warning(f"RSS parse xatosi: {e}")

        return items

    async def get_ai_news(self, count: int = 3) -> list[dict]:
        """Barcha manbalardan yangilik yig'ib, eng dolzarblarini qaytaradi."""
        all_items: list[dict] = []
        for feed_url in AI_NEWS_FEEDS:
            items = await self.fetch_rss(feed_url, limit=8)
            all_items.extend(items)

        # Takrorlanganlarni olib tashlash (sarlavha bo'yicha)
        seen, unique = set(), []
        for item in all_items:
            key = item["title"][:60].lower()
            if key not in seen:
                seen.add(key)
                unique.append(item)

        return unique[:count]

    async def generate_educational_post(self, topic: str, style: str = DEFAULT_STYLE) -> str:
        """Berilgan AI mavzuda o'zbek tilida ta'limiy post yaratadi."""
        prompt = f"""
Sen sun'iy intellekt sohasida 8-10 yillik tajribaga ega, o'z auditoriyasiga ega bo'lgan ekspert-muallifsan. Telegram kanalingga quyidagi mavzu bo'yicha post yozasan.

Mavzu: {topic}

{style_instruction(style)}

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

    async def generate_news_post(self, news_items: list[dict], style: str = DEFAULT_STYLE) -> str:
        """Yangiliklar ro'yxatini o'zbek tilidagi Telegram postga aylantiradi."""
        if not news_items:
            return await self._generate_fallback_news_post()

        news_text = "\n\n".join(
            f"{i+1}. {item['title']}\n{item['desc']}"
            for i, item in enumerate(news_items)
        )

        prompt = f"""
Sen sun'iy intellekt yangiliklarini kuzatib boradigan, o'z auditoriyasiga ega tajribali tahlilchi-muallifsan. Quyidagi inglizcha manbalar asosida Telegram kanalga o'zbek tilida post tayyorla.

Manbalar:
{news_text}

{style_instruction(style)}

Talablar:
- Jonli inson yozgandek yoz — quruq tarjima yoki shablon ko'rinishidan qoch. Har bir yangilikka o'z munosabatingni yoki qisqa sharhingni qo'sh (nega bu muhim, nima o'zgaradi).
- To'g'ri adabiy o'zbek tilida yoz. Tarjimada ma'noni to'liq va aniq yetkazishga harakat qil.
- Grammatika xatolariga yo'l qo'yma. Jumlalar ravon o'qilsin.
- Emojilarni sarlavhalarda va matn ichida tabiiy tarzda ishlat — mazmunni jonlantirish uchun, lekin haddan oshirmasdan.
- Telegram Markdown formatida yoz (**qalin**, _kursiv_).
- Har bir yangilik uchun 2–3 ta aniq va mazmunan to'g'ri gap yoz.
- Quyidagi tuzilishda yoz:

🌐 **AI Yangiliklari**

1️⃣ **[1-yangilik sarlavhasi — o'zbekcha, aniq]**
[Qisqa mazmun + o'z sharhing, 2–3 gap. Asosiy g'oyani tushunarli tarzda yetkazib ber.]

2️⃣ **[2-yangilik sarlavhasi — o'zbekcha, aniq]**
[Qisqa mazmun + o'z sharhing, 2–3 gap.]

3️⃣ **[3-yangilik sarlavhasi — o'zbekcha, aniq]**
[Qisqa mazmun + o'z sharhing, 2–3 gap.]

#AI #SuniyIntellekt #Yangiliklar #Texnologiya
"""
        return await self._call_llm(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=800,
        )

    async def generate_free_post(self, topic: str, style: str = DEFAULT_STYLE) -> str:
        """Ega bergan erkin mavzu/topshiriq bo'yicha post yozadi."""
        prompt = f"""
Sen o'z auditoriyasiga ega, tajribali Telegram kanal muallifisan. Quyidagi mavzu/topshiriq bo'yicha kanalingga o'zbek tilida post yoz.

Mavzu / topshiriq: {topic}

{style_instruction(style)}

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

    async def _generate_fallback_news_post(self) -> str:
        """Yangilik topilmasa, umumiy AI haqida post yozadi."""
        prompt = """
Sen sun'iy intellekt sohasidagi tajribali ekspert-muallifsan. Bugungi eng dolzarb 3 ta tendensiya yoki yangilik haqida
Telegram kanalga o'zbek tilida, jonli inson yozgandek (shablon emas), o'z fikringni ham qo'shib post tayyorla.
Emojilarni matn ichida tabiiy ishlat. Grammatikaga e'tibor ber, jumlalar ravon bo'lsin.

Tuzilish:
🌐 **AI Yangiliklari**

1️⃣ **[Sarlavha]**
[2–3 gap]

2️⃣ **[Sarlavha]**
[2–3 gap]

3️⃣ **[Sarlavha]**
[2–3 gap]

#AI #SuniyIntellekt #Yangiliklar #Texnologiya
"""
        return await self._call_llm(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=600,
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
        """Yangiliklar ro'yxatini tasodifiy tartibda qaytaradi (boshqa post uchun)."""
        import random
        all_items: list[dict] = []
        for feed_url in AI_NEWS_FEEDS:
            items = await self.fetch_rss(feed_url, limit=12)
            all_items.extend(items)

        seen, unique = set(), []
        for item in all_items:
            key = item["title"][:60].lower()
            if key not in seen:
                seen.add(key)
                unique.append(item)

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
