"""O'zbekiston ob-havosi — kanal uchun ertalabki post.

Ma'lumot Open-Meteo dan olinadi: bepul, API kalit talab qilmaydi, barcha
shaharlar BITTA so'rovda keladi.

MUHIM QOIDA — raqamlarni LLM YOZMAYDI. Harorat, yog'ingarchilik va shahar
nomlari to'g'ridan-to'g'ri API javobidan shablonga qo'yiladi. Modelga faqat
bir gaplik maslahat yozish topshiriladi va unga "raqam yozma" deb aytiladi.
Aks holda agent ishonch bilan noto'g'ri harorat e'lon qilishi mumkin edi —
ob-havo posti uchun bu eng yomon nosozlik.
"""
from datetime import datetime

import httpx
import pytz
from loguru import logger

API_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT = 20
TZ = pytz.timezone("Asia/Tashkent")

# Viloyat markazlari — tartib postdagi tartibni belgilaydi (Toshkent birinchi)
CITIES: list[tuple[str, float, float]] = [
    ("Toshkent",  41.31, 69.24),
    ("Nukus",     42.46, 59.61),
    ("Urganch",   41.55, 60.63),
    ("Buxoro",    39.77, 64.42),
    ("Navoiy",    40.10, 65.37),
    ("Samarqand", 39.65, 66.96),
    ("Qarshi",    38.86, 65.79),
    ("Termiz",    37.22, 67.28),
    ("Jizzax",    40.12, 67.84),
    ("Guliston",  40.49, 68.78),
    ("Namangan",  41.00, 71.67),
    ("Andijon",   40.78, 72.34),
    ("Farg'ona",  40.39, 71.78),
]

# WMO ob-havo kodlari → (emoji, o'zbekcha tavsif)
_WEATHER_CODES: dict[int, tuple[str, str]] = {
    0:  ("☀️", "ochiq"),
    1:  ("🌤", "asosan ochiq"),
    2:  ("⛅️", "bulutli"),
    3:  ("☁️", "to'liq bulutli"),
    45: ("🌫", "tuman"),
    48: ("🌫", "qirovli tuman"),
    51: ("🌦", "mayda yomg'ir"),
    53: ("🌦", "yomg'ir"),
    55: ("🌧", "kuchli mayda yomg'ir"),
    61: ("🌦", "yengil yomg'ir"),
    63: ("🌧", "yomg'ir"),
    65: ("🌧", "kuchli yomg'ir"),
    71: ("🌨", "yengil qor"),
    73: ("🌨", "qor"),
    75: ("❄️", "kuchli qor"),
    77: ("🌨", "qor donalari"),
    80: ("🌦", "jala"),
    81: ("🌧", "kuchli jala"),
    82: ("⛈", "juda kuchli jala"),
    85: ("🌨", "qor jalasi"),
    86: ("❄️", "kuchli qor jalasi"),
    95: ("⛈", "momaqaldiroq"),
    96: ("⛈", "do'l bilan momaqaldiroq"),
    99: ("⛈", "kuchli do'l"),
}

# Shu foizdan past yog'ingarchilik ehtimoli postda ko'rsatilmaydi — har qatorga
# "0%" yozish postni shovqinga to'ldiradi.
RAIN_MENTION_THRESHOLD = 30

_MONTHS = [
    "yanvar", "fevral", "mart", "aprel", "may", "iyun",
    "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr",
]
_WEEKDAYS = [
    "dushanba", "seshanba", "chorshanba", "payshanba",
    "juma", "shanba", "yakshanba",
]


def describe_code(code: int | None) -> tuple[str, str]:
    """WMO kodini emoji va o'zbekcha tavsifga o'giradi."""
    return _WEATHER_CODES.get(code if code is not None else -1, ("🌡", "aniqlanmadi"))


def uzbek_date(when: datetime | None = None) -> str:
    d = when or datetime.now(TZ)
    return f"{d.day}-{_MONTHS[d.month - 1]}, {_WEEKDAYS[d.weekday()]}"


async def fetch() -> list[dict] | None:
    """Barcha shaharlar uchun bugungi prognozni oladi (bitta so'rov).

    Xato bo'lsa None — chaqiruvchi post yubormasligi kerak. Noto'g'ri
    ob-havodan ko'ra post bo'lmagani yaxshi.
    """
    params = {
        "latitude": ",".join(str(lat) for _, lat, _ in CITIES),
        "longitude": ",".join(str(lon) for _, _, lon in CITIES),
        "daily": "temperature_2m_max,temperature_2m_min,"
                 "precipitation_probability_max,weather_code",
        "timezone": "Asia/Tashkent",
        "forecast_days": 1,
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(API_URL, params=params)
            response.raise_for_status()
            payload = response.json()
    except Exception as e:
        logger.error(f"Ob-havo ma'lumotini olishda xato: {e}")
        return None

    # Bitta shahar so'ralsa API ro'yxat emas, obyekt qaytaradi
    entries = payload if isinstance(payload, list) else [payload]
    if len(entries) != len(CITIES):
        logger.error(
            f"Ob-havo javobi to'liq emas: {len(entries)}/{len(CITIES)} shahar"
        )
        return None

    result = []
    for (name, _, _), entry in zip(CITIES, entries):
        daily = entry.get("daily") or {}
        try:
            result.append({
                "city": name,
                "min": round(daily["temperature_2m_min"][0]),
                "max": round(daily["temperature_2m_max"][0]),
                "rain": daily["precipitation_probability_max"][0] or 0,
                "code": daily["weather_code"][0],
            })
        except (KeyError, IndexError, TypeError) as e:
            logger.error(f"Ob-havo maydoni o'qilmadi ({name}): {e}")
            return None
    return result


def _signed(value: int) -> str:
    return f"+{value}" if value > 0 else str(value)


def format_post(rows: list[dict], comment: str = "") -> str:
    """Ma'lumotni kanal posti matniga aylantiradi (HTML parse_mode)."""
    lines = [f"🌤 <b>Ob-havo — {uzbek_date()}</b>", ""]

    for row in rows:
        emoji, _ = describe_code(row["code"])
        temps = f"{_signed(row['min'])}…{_signed(row['max'])}°"
        rain = (
            f"  💧{row['rain']}%"
            if row["rain"] >= RAIN_MENTION_THRESHOLD else ""
        )
        lines.append(f"{emoji} <b>{row['city']}</b> — {temps}{rain}")

    if comment:
        lines += ["", f"💬 {comment}"]

    lines += ["", "#obhavo #ozbekiston"]
    return "\n".join(lines)


def summarize(rows: list[dict]) -> dict:
    """Izoh yozish uchun asosiy faktlar (LLM ga aynan shular beriladi)."""
    hottest = max(rows, key=lambda r: r["max"])
    coldest = min(rows, key=lambda r: r["min"])
    rainy = [r["city"] for r in rows if r["rain"] >= RAIN_MENTION_THRESHOLD]
    return {
        "eng_issiq": f"{hottest['city']} {hottest['max']}°",
        "eng_salqin": f"{coldest['city']} {coldest['min']}°",
        "yomgirli_shaharlar": rainy,
        "toshkent": next(
            (f"{r['min']}…{r['max']}°" for r in rows if r["city"] == "Toshkent"), ""
        ),
        "ob_havo": describe_code(rows[0]["code"])[1],
    }
