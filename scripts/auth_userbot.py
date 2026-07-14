#!/usr/bin/env python
"""UserBot sessiyasini interaktiv avtorizatsiya qiladi.

Telethon userbot sessiyasi (`sessions/<TELEGRAM_SESSION_NAME>.session`) muddati
tugaganda yoki birinchi marta sozlanayotganda ishlatiladi. Telefon raqami .env
dan olinadi; tasdiqlash kodi (va 2FA paroli, agar yoqilgan bo'lsa) terminal
orqali interaktiv so'raladi.

Ishga tushirish (loyiha ildizidan):

    docker run --rm -it --network docker_agent-net \
      --env-file .env \
      -v "$PWD/sessions:/app/sessions" \
      -v "$PWD/scripts:/app/scripts" \
      docker-app /app/.venv/bin/python /app/scripts/auth_userbot.py

Muvaffaqiyatli tugagach, `docker compose -f docker/docker-compose.yml restart app`
bilan agentni qayta ishga tushiring — endi userbot xabarlarga javob beradi.
"""
import os
import sys
from pathlib import Path

from telethon.sync import TelegramClient


def main() -> int:
    try:
        api_id = int(os.environ["TELEGRAM_API_ID"])
        api_hash = os.environ["TELEGRAM_API_HASH"]
        phone = os.environ["TELEGRAM_PHONE"]
    except KeyError as e:
        print(f"❌ Muhit o'zgaruvchisi yo'q: {e}. .env to'g'ri ulanganmi?")
        return 1

    session_name = os.environ.get("TELEGRAM_SESSION_NAME", "Shaxzodbek")
    # App bilan bir xil joy: telegram_service.py dagi Path("sessions") / session_name
    session_path = Path("sessions") / session_name
    session_path.parent.mkdir(exist_ok=True)

    print(f"📱 Avtorizatsiya: {phone}  →  sessiya: {session_path}.session")
    print("   Telegram yuborgan kodni (va kerak bo'lsa 2FA parolini) kiriting.\n")

    client = TelegramClient(str(session_path), api_id, api_hash)
    client.start(phone=phone)  # interaktiv: kod + (ixtiyoriy) 2FA parol so'raydi

    me = client.get_me()
    client.disconnect()

    uname = f"@{me.username}" if me.username else "(username yo'q)"
    print(
        f"\n✅ Muvaffaqiyatli! Avtorizatsiya qilindi: "
        f"{me.first_name or ''} {uname}, id={me.id}"
    )
    print("   Endi agentni qayta ishga tushiring:")
    print("   docker compose -f docker/docker-compose.yml restart app")
    return 0


if __name__ == "__main__":
    sys.exit(main())
