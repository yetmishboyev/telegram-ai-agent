import httpx
import xml.etree.ElementTree as ET
from loguru import logger

from app.ai.agents.base_agent import BaseAgent


# Sun'iy intellektga oid RSS manbalar
AI_NEWS_FEEDS = [
    "https://news.google.com/rss/search?q=artificial+intelligence+AI&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=AI+conference+grant+2025&hl=en-US&gl=US&ceid=US:en",
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


class NewsFetcher(BaseAgent):

    async def run(self, *args, **kwargs):
        pass

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

    async def generate_educational_post(self, topic: str) -> str:
        """Berilgan AI mavzuda o'zbek tilida ta'limiy post yaratadi."""
        prompt = f"""
Sen sun'iy intellekt va texnologiya sohasidagi mutaxassissan. Quyidagi mavzu bo'yicha Telegram kanalga post tayyorla.

Mavzu: {topic}

Talablar:
- To'g'ri adabiy o'zbek tilida yoz. Grammatika xatolariga yo'l qo'yma.
- So'zlarni to'g'ri qo'lla: "sun'iy intellekt" (suniy emas), "dastur" (dasturiy ta'minot), "foydalanuvchi" va hokazo.
- Telegram Markdown formatida yoz (**qalin**, _kursiv_).
- Hajmi: 220–280 so'z.
- Quyidagi tuzilishda yoz:

🎓 **[Mavzu sarlavhasi — aniq va qisqa]**

📌 **Nima bu?**
[Oddiy va tushunarli tushuntirish, 2–3 gap. Atamani kundalik hayot bilan bog'lab tushuntir.]

💡 **Qanday ishlaydi?**
[Qisqacha texnik tushuntirish. Mumkin bo'lsa, o'xshashlik yoki misol keltir.]

🚀 **Qayerda qo'llaniladi?**
[2–3 ta real hayotiy misol — ChatGPT, Google, tibbiyot, ta'lim va boshqalar.]

🔗 **Nima uchun muhim?**
[Ushbu texnologiyaning sun'iy intellekt taraqqiyotidagi o'rni, 1–2 gap.]

#SuniyIntellekt #AI #Texnologiya #Dars
"""
        return await self._call_llm(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=700,
        )

    async def generate_news_post(self, news_items: list[dict]) -> str:
        """Yangiliklar ro'yxatini o'zbek tilidagi Telegram postga aylantiradi."""
        if not news_items:
            return await self._generate_fallback_news_post()

        news_text = "\n\n".join(
            f"{i+1}. {item['title']}\n{item['desc']}"
            for i, item in enumerate(news_items)
        )

        prompt = f"""
Sen sun'iy intellekt yangiliklari tahlilchisisisan. Quyidagi inglizcha manbalar asosida Telegram kanalga o'zbek tilidagi post tayyorla.

Manbalar:
{news_text}

Talablar:
- To'g'ri adabiy o'zbek tilida yoz. Tarjimada ma'noni to'liq va aniq yetkazishga harakat qil.
- Grammatika xatolariga yo'l qo'yma. Jumlalar ravon o'qilsin.
- Telegram Markdown formatida yoz (**qalin**, _kursiv_).
- Har bir yangilik uchun 2–3 ta aniq va mazmunan to'g'ri gap yoz.
- Quyidagi tuzilishda yoz:

🌐 **AI Yangiliklari**

1️⃣ **[1-yangilik sarlavhasi — o'zbekcha, aniq]**
[Qisqa mazmun, 2–3 gap. Asosiy g'oyani tushunarli tarzda yetkazib ber.]

2️⃣ **[2-yangilik sarlavhasi — o'zbekcha, aniq]**
[Qisqa mazmun, 2–3 gap.]

3️⃣ **[3-yangilik sarlavhasi — o'zbekcha, aniq]**
[Qisqa mazmun, 2–3 gap.]

#AI #SuniyIntellekt #Yangiliklar #Texnologiya
"""
        return await self._call_llm(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=800,
        )

    async def _generate_fallback_news_post(self) -> str:
        """Yangilik topilmasa, umumiy AI haqida post yozadi."""
        prompt = """
Sun'iy intellekt sohasidagi bugungi eng dolzarb 3 ta tendensiya yoki yangilik haqida
Telegram kanalga o'zbek tilida post tayyorla. Grammatikaga e'tibor ber, jumlalar ravon bo'lsin.

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
- To'g'ri adabiy o'zbek tilida, ravon va qiziqarli yoz.
- HTML formatida yoz: sarlavhalar <b>qalin</b>, bo'limlar orasida bo'sh qator.
- Har bir post uchun 1-2 jumlada qisqacha mazmun ber.
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
