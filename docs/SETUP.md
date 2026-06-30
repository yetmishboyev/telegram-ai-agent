# Telegram AI Agent — O'rnatish va Ishga Tushirish

## Talablar

- Python 3.13+
- Docker & Docker Compose
- Telegram API ID va HASH (my.telegram.org dan olinadi)
- Anthropic yoki OpenAI API kaliti

---

## 1-qadam: API kalitlarini olish

### Telegram API
1. https://my.telegram.org/auth ga kiring
2. "API Development Tools" bo'limiga o'ting
3. Yangi ilova yarating
4. `api_id` va `api_hash` ni oling

### Anthropic API
1. https://console.anthropic.com ga kiring
2. API Keys → Create Key

---

## 2-qadam: Konfiguratsiya

```bash
cd telegram-ai-agent
cp .env.example .env
```

`.env` faylini to'ldiring:

```env
TELEGRAM_API_ID=1234567
TELEGRAM_API_HASH=abc123...
TELEGRAM_PHONE=+998901234567
AI_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
SECRET_KEY=kamida-32-ta-belgidan-iborat-kalit
ADMIN_PASSWORD=kuchli-parol
```

---

## 3-qadam: Birinchi sessiya (muhim!)

Birinchi marta Telegram sessiyasini yaratish uchun interaktiv rejimda ishga tushirish kerak:

```bash
# Virtual muhit va paketlarni o'rnatish
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install uv
uv sync

# Birinchi sessiya
python -c "
import asyncio
from telethon import TelegramClient
from app.config import settings

async def create_session():
    client = TelegramClient('sessions/' + settings.telegram_session_name, settings.telegram_api_id, settings.telegram_api_hash)
    await client.start(phone=settings.telegram_phone)
    me = await client.get_me()
    print(f'Ulandi: {me.first_name} (@{me.username})')
    await client.disconnect()

asyncio.run(create_session())
"
```

Telefon raqamingizga SMS kod keladi, uni kiriting.

---

## 4-qadam: Docker bilan ishga tushirish

```bash
cd docker
docker compose up -d --build
```

---

## 5-qadam: Dashboard

Brauzerda oching: http://localhost:8000/dashboard/login

Login: `.env` dagi `ADMIN_USERNAME` va `ADMIN_PASSWORD`

---

## Buyruqlar

```bash
# Loglarni ko'rish
docker compose logs -f app

# To'xtatish
docker compose down

# DB migratsiya
uv run alembic revision --autogenerate -m "init"
uv run alembic upgrade head

# Testlar
uv run pytest tests/ -v
```

---

## Xavfsizlik tekshiruvi

- [ ] `.env` faylini hech qachon git ga qo'shmang
- [ ] `SECRET_KEY` kamida 64 ta tasodifiy belgi bo'lsin
- [ ] `ADMIN_PASSWORD` kuchli parol bo'lsin
- [ ] `sessions/` papkasini zaxiralang
- [ ] Production da `docs_url=None` qolishi kerak (avtomatik)
- [ ] Docker network faqat ichki (`agent-net`) orqali ishlaydi
