"""LLM javoblaridan JSON ajratib olish — barcha agentlar uchun umumiy yordamchi.

Model ba'zan JSON'ni ```json ...``` qatlamiga o'raydi, ba'zan xom holda,
ba'zan esa oldin/keyin qo'shimcha matn bilan qaytaradi. Bu funksiya uchala
holatni ham qamrab oladi.
"""
import json
import re

_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def parse_json_response(raw: str) -> dict | list:
    """LLM javobidan JSON obyekt/ro'yxatni ajratib qaytaradi.

    Ketma-ket 3 usulni sinaydi:
      1. ```json ...``` yoki ``` ...``` qatlamini olib tashlab to'g'ridan-to'g'ri parse.
      2. Qatlamsiz xom matnni to'g'ridan-to'g'ri parse.
      3. Matn ichidan birinchi {...} yoki [...] blokini ajratib parse.

    Muvaffaqiyatsiz bo'lsa `json.JSONDecodeError` ko'taradi — chaqiruvchi
    o'zining mavjud `except (json.JSONDecodeError, ValueError)` blokini
    o'zgartirmasdan ishlatishi mumkin.
    """
    text = raw.strip()

    fence_match = _FENCE_RE.match(text)
    if fence_match:
        text = fence_match.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Birinchi { yoki [ dan boshlab TO'LIQ obyektni raw_decode bilan olamiz —
    # greedy regex oxirgi } gacha qamrab, JSONdan keyingi matnda "Extra data"
    # xatosi berardi (model ba'zan JSONdan keyin izoh yozadi).
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "{[":
            try:
                obj, _ = decoder.raw_decode(text, i)
                return obj
            except json.JSONDecodeError:
                continue

    raise json.JSONDecodeError("Javobda JSON topilmadi", raw, 0)
