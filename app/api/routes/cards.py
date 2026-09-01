"""Kanal postlarining muqova kartalarini tarqatadi.

Nega ochiq (parolsiz) yo'l: Telegram post ichidagi havolani O'ZI yuklab
oladi va rasmni ko'rsatadi. Uning sessiyasi yo'q, shuning uchun tokenli
yo'l ishlamaydi — karta ochiq bo'lishi shart.

Nega bu xavfsiz: bu yerda faqat kanalga chiqadigan postning muqovasi
turadi — sarlavha, bitta sitata va kanal nomi. Hammasi baribir ommaviy
kanalda chiqadi. Shaxsiy ma'lumot, foydalanuvchi yozishmalari yoki
tizim holati bu yerga tushmaydi.

Kalit tasodifiy (uuid4), ro'yxat qaytaradigan yo'l yo'q va faqat GET
qabul qilinadi — ya'ni manzilni bilmagan odam kartalarni sanab chiqa
olmaydi.

Karta Redisda BASE64 ko'rinishida yotadi: umumiy client `decode_responses=True`
bilan ochilgan, ya'ni xom PNG baytlari o'qishda UTF-8 deb talqin qilinib
buzilardi. Ikkinchi ulanish puli ochishdan ko'ra ~33% hajm arzonroq.
"""
import re

from fastapi import APIRouter, HTTPException, Response
from loguru import logger

router = APIRouter(prefix="/p", tags=["cards"])

# Redisda karta shu prefiks bilan yotadi
CARD_KEY = "post_card:{}"

# Telegram havolani post yuborilgandan keyin ham qayta so'rashi mumkin
# (kesh yangilanganda), shuning uchun karta bir oy saqlanadi.
CARD_TTL = 60 * 60 * 24 * 30

_ID_RE = re.compile(r"^[0-9a-f]{8,32}$")


@router.get("/{card_id}.png")
async def get_post_card(card_id: str) -> Response:
    """Muqova kartasini PNG sifatida qaytaradi."""
    # Kalit Redis so'roviga qo'shilgani uchun shakli qat'iy tekshiriladi
    if not _ID_RE.match(card_id):
        raise HTTPException(status_code=404, detail="Karta topilmadi")

    try:
        from app.database.redis import get_redis
        r = await get_redis()
        data = await r.get(CARD_KEY.format(card_id))
    except Exception as e:
        logger.error(f"Kartani o'qishda xato ({card_id}): {e}")
        raise HTTPException(status_code=503, detail="Karta hozir mavjud emas")

    if not data:
        raise HTTPException(status_code=404, detail="Karta topilmadi")

    import base64
    try:
        png = base64.b64decode(data)
    except Exception as e:
        logger.error(f"Karta base64 dan ochilmadi ({card_id}): {e}")
        raise HTTPException(status_code=404, detail="Karta topilmadi")

    return Response(
        content=png,
        media_type="image/png",
        # Telegram va boshqa keshlar rasmni qayta-qayta so'ramasin
        headers={"Cache-Control": "public, max-age=604800, immutable"},
    )
