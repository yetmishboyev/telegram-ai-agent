# Telegram AI Agent

Shaxsiy Telegram AI agent — xabarlarni avtomatik qayta ishlash, kanal boshqaruvi va kunlik reja.

## Imkoniyatlar

### AI Agent
- Kiruvchi xabarlarni avtomatik tahlil qilib javob beradi
- Egasi yozayotganda AI jim turadi (owner-active tracking)
- Bir nechta xabar kelsa bitta javob qaytaradi (batch/debounce)
- Foydalanuvchi uslubini o'rganib, shunga moslashadi

### Telegram Kanal (@Yetmishboyev_Sh)
- **Dushanba–Juma:** 09:00 ta'limiy post, 12:00 va 16:00 yangiliklar
- **Shanba:** dam olish kuni — hech qanday post yo'q
- **Yakshanba:** 12:00 haftalik dayjest — eng yaxshi postlar + linklar
- Har bir post egaga tasdiqlashga yuboriladi (✅ / ✏️ / 🔄 / ❌)

### Kunlik Reja (Bot)
- Bot orqali kunlik vazifalar qo'shish va boshqarish
- Har kuni ertalab 07:00 da eslatma
- Joriy jadvalga qarab dinamik AI javob

### Dashboard
- Real-vaqt statistika va monitoring
- Suhbatlar, xotira, loglar
- Kanal analitika — ko'rishlar, eng yaxshi postlar, PR tavsiyalar

## Texnologiyalar

- **Backend:** Python, FastAPI, Telethon
- **AI:** Claude API (Anthropic)
- **Ma'lumotlar bazasi:** PostgreSQL, Redis, ChromaDB
- **Deploy:** Docker Compose

## Loyiha tuzilmasi

```
app/
├── ai/          # AI agentlar va promptlar
├── api/         # FastAPI routerlar
├── database/    # Modellar va sessiya
├── repositories/
├── services/    # Telegram, bot, kanal, scheduler
dashboard/       # Web panel (HTML/Alpine.js)
docker/          # Docker Compose konfiguratsiya
```

## Muallif

**Yetmishboyev Shaxzodbek**
