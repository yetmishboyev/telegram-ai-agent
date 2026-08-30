"""Ob-havo kartasini rasm sifatida chizadi.

Nega HTML emas: HTML ni haqiqiy renderlash uchun brauzer (Chromium) kerak —
u ~400 MB disk va render paytida 300-500 MB RAM oladi. Serverda ~450 MB
bo'sh joy bor va agentning o'zi 1 GB ishlatadi (embedding modeli), ya'ni
07:00 dagi vazifa OOM bilan yiqilishi mumkin edi. Pillow ~50 MB da ishlaydi.

Ob-havo BELGILARI ham chizib chiqiladi, emoji sifatida yozilmaydi: rangli
emoji shrifti alohida bog'liqlik bo'lardi va Pillow'da uni ishlatish
platformaga bog'liq. Chizilgan belgi hamma joyda bir xil ko'rinadi.

Rasm yaratilmasa `None` qaytadi va chaqiruvchi oddiy matnli postga qaytadi —
rasm hech qachon postni to'sib qo'ymasligi kerak.
"""
import io
from pathlib import Path

from loguru import logger

W = 1080
MARGIN = 72
ROW_H = 74
HEAD_H = 246          # sarlavha bloki
FOOT_H = 150          # pastki qator + ajratgich

def canvas_height(row_count: int) -> int:
    """Balandlik qatorlar soniga moslashadi — pastda bo'sh joy qolmaydi."""
    return HEAD_H + row_count * ROW_H + FOOT_H

# Ranglar — tungi ko'k fon, issiqlikka qarab rangli harorat
BG_TOP = (18, 32, 58)
BG_BOTTOM = (38, 62, 102)
INK = (255, 255, 255)
INK_SOFT = (168, 186, 214)
DIVIDER = (255, 255, 255, 28)

# Harorat rangi — bir qarashda issiq/salqinni ko'rsatadi
def temp_color(celsius: int) -> tuple[int, int, int]:
    if celsius >= 38:
        return (255, 122, 89)      # jazirama
    if celsius >= 30:
        return (255, 176, 84)      # issiq
    if celsius >= 20:
        return (255, 224, 130)     # iliq
    if celsius >= 8:
        return (146, 224, 178)     # salqin
    return (137, 196, 244)         # sovuq


# Shrift qidiriladigan joylar (Docker: fonts-dejavu-core)
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
]


def _font(size: int, bold: bool = False):
    from PIL import ImageFont

    names = _FONT_CANDIDATES
    if bold:
        names = sorted(names, key=lambda p: "Bold" not in p)
    for path in names:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default(size)


def _gradient(draw, H: int) -> None:
    """Yuqoridan pastga tekis o'tish — bitta piksel qatori bilan chiziladi."""
    for y in range(H):
        t = y / H
        draw.line(
            [(0, y), (W, y)],
            fill=tuple(
                round(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3)
            ),
        )


# ─── ob-havo belgilari (chiziladi, emoji emas) ────────────────────────────────

def _sun(draw, cx: int, cy: int, r: int = 15) -> None:
    gold = (255, 205, 92)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=gold)
    for i in range(8):
        import math
        a = math.pi * i / 4
        x1, y1 = cx + math.cos(a) * (r + 6), cy + math.sin(a) * (r + 6)
        x2, y2 = cx + math.cos(a) * (r + 12), cy + math.sin(a) * (r + 12)
        draw.line([x1, y1, x2, y2], fill=gold, width=3)


def _cloud(draw, cx: int, cy: int, color=(214, 226, 242)) -> None:
    draw.ellipse([cx - 20, cy - 6, cx + 2, cy + 14], fill=color)
    draw.ellipse([cx - 6, cy - 15, cx + 18, cy + 12], fill=color)
    draw.ellipse([cx + 6, cy - 4, cx + 26, cy + 14], fill=color)
    draw.rectangle([cx - 16, cy + 4, cx + 22, cy + 14], fill=color)


def _rain(draw, cx: int, cy: int) -> None:
    _cloud(draw, cx, cy - 6, (176, 194, 216))
    for dx in (-12, 0, 12):
        draw.line([cx + dx, cy + 14, cx + dx - 4, cy + 26],
                  fill=(126, 186, 240), width=3)


def _snow(draw, cx: int, cy: int) -> None:
    _cloud(draw, cx, cy - 6, (200, 214, 232))
    for dx in (-12, 0, 12):
        draw.ellipse([cx + dx - 3, cy + 16, cx + dx + 3, cy + 22], fill=(226, 240, 255))


def _storm(draw, cx: int, cy: int) -> None:
    """Chaqmoq qator balandligiga sig'ishi kerak — aks holda ajratgichni kesib o'tadi."""
    _cloud(draw, cx, cy - 10, (150, 166, 190))
    draw.polygon(
        [(cx + 1, cy + 6), (cx - 7, cy + 20), (cx - 1, cy + 20),
         (cx - 4, cy + 30), (cx + 9, cy + 14), (cx + 2, cy + 14)],
        fill=(255, 206, 84),
    )


def draw_icon(draw, code: int | None, cx: int, cy: int) -> None:
    """WMO kodiga mos belgini chizadi."""
    c = code if code is not None else 0
    if c in (0, 1):
        _sun(draw, cx, cy)
    elif c in (2, 3, 45, 48):
        _cloud(draw, cx, cy)
    elif c in (71, 73, 75, 77, 85, 86):
        _snow(draw, cx, cy)
    elif c in (95, 96, 99):
        _storm(draw, cx, cy)
    elif c >= 51:
        _rain(draw, cx, cy)
    else:
        _cloud(draw, cx, cy)


# ─── karta ─────────────────────────────────────────────────────────────────────

def render(rows: list[dict], date_text: str) -> bytes | None:
    """Ob-havo kartasini PNG baytlari sifatida qaytaradi (xato bo'lsa None)."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        logger.warning("Pillow o'rnatilmagan — ob-havo rasmi chizilmaydi")
        return None

    if not rows:
        return None

    try:
        H = canvas_height(len(rows))
        image = Image.new("RGB", (W, H), BG_TOP)
        draw = ImageDraw.Draw(image, "RGBA")
        _gradient(draw, H)

        f_label = _font(30, bold=True)
        f_date = _font(52, bold=True)
        f_city = _font(38)
        f_temp = _font(38, bold=True)
        f_foot = _font(28)

        # Sarlavha
        draw.text((MARGIN, 74), "OB-HAVO", font=f_label, fill=INK_SOFT)
        draw.text((MARGIN, 118), date_text.upper(), font=f_date, fill=INK)
        draw.line([MARGIN, 208, W - MARGIN, 208], fill=DIVIDER, width=2)

        # Qatorlar
        y = HEAD_H
        for row in rows:
            draw_icon(draw, row.get("code"), MARGIN + 22, y + ROW_H // 2 - 4)
            draw.text((MARGIN + 68, y + 14), row["city"], font=f_city, fill=INK)

            temps = f"{row['min']:+d}° … {row['max']:+d}°"
            right = W - MARGIN
            width = draw.textlength(temps, font=f_temp)
            draw.text((right - width, y + 14), temps, font=f_temp,
                      fill=temp_color(row["max"]))

            if row.get("rain", 0) >= 30:
                mark = f"{row['rain']}%"
                mw = draw.textlength(mark, font=f_foot)
                draw.text((right - width - mw - 28, y + 20), mark,
                          font=f_foot, fill=(137, 196, 244))

            y += ROW_H
            if row is not rows[-1]:
                draw.line([MARGIN, y - 6, W - MARGIN, y - 6], fill=DIVIDER, width=1)

        # Pastki qator
        draw.line([MARGIN, H - 104, W - MARGIN, H - 104], fill=DIVIDER, width=2)
        draw.text((MARGIN, H - 78), "@Yetmishboyev_Sh", font=f_foot, fill=INK_SOFT)
        source = "Open-Meteo"
        sw = draw.textlength(source, font=f_foot)
        draw.text((W - MARGIN - sw, H - 78), source, font=f_foot, fill=INK_SOFT)

        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()
    except Exception as e:
        logger.error(f"Ob-havo rasmini chizishda xato: {e}")
        return None
