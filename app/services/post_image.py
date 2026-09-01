"""Kanal postlari uchun muqova kartasini chizadi.

Nega Pillow: `weather_image.py` dagi bilan bir xil sabab — HTML ni haqiqiy
renderlash uchun Chromium kerak (~400 MB disk, render paytida 300-500 MB RAM),
serverda esa ~450 MB bo'sh joy bor va agentning o'zi 1 GB ishlatadi. Pillow
~50 MB da ishlaydi va ob-havo kartasi bilan bitta uslub beradi.

Nega generatsiya qilingan rasm emas: har post uchun tasvir modeli chaqirilsa
oyiga qo'shimcha xarajat va +5-15s kechikish qo'shiladi, sifati esa har xil
chiqadi. Muqova karta bepul, ~0.2s da chiziladi va natijasi oldindan ma'lum.

EMOJI CHIZILMAYDI: DejaVu (konteynerdagi yagona shrift) rangli emojini
bilmaydi va u tўrtburchak bo'lib chiqadi. Post TURI kichik geometrik belgi
bilan ko'rsatiladi — ob-havo belgilaridagi yondashuvning aynan o'zi.

Karta chizilmasa `None` qaytadi va chaqiruvchi postni matn ko'rinishida
yuboradi — rasm hech qachon postni to'sib qo'ymasligi kerak.
"""
import io
import re

from loguru import logger

W = H = 1080
MARGIN = 84
CHANNEL = "@Yetmishboyev_Sh"

# Post turi → (yorliq, fon yuqori, fon past, urg'u rangi).
# Rang postni bir qarashda ajratadi: obunachi lentada turini o'qimasdan biladi.
STYLES: dict[str, tuple[str, tuple, tuple, tuple]] = {
    "educational": ("TA'LIMIY",          (16, 34, 62),  (34, 68, 116), (126, 179, 255)),
    "practical":   ("AMALIY QO'LLANMA",  (12, 44, 38),  (26, 84, 68),  (116, 224, 178)),
    "tool":        ("VOSITA SHARHI",     (30, 26, 62),  (58, 50, 112), (176, 158, 255)),
    "news":        ("YANGILIK",          (52, 24, 28),  (98, 44, 44),  (255, 150, 128)),
    "digest":      ("HAFTALIK DAYJEST",  (46, 36, 14),  (88, 70, 28),  (255, 202, 106)),
    "free":        ("POST",              (22, 32, 44),  (44, 62, 84),  (156, 190, 220)),
}
FALLBACK = STYLES["free"]

INK = (255, 255, 255)
INK_SOFT = (176, 192, 214)
DIVIDER = (255, 255, 255, 34)


def _tint(accent: tuple, amount: float = 0.45) -> tuple:
    """Urg'u rangini oqartiradi — ost-sarlavha uchun.

    Barcha turlarda bitta kulrang ishlatilsa, issiq fonli kartada (yangilik,
    dayjest) u ko'kish bo'lib, fondan ajralib turardi. Rang har turning o'z
    palitrasidan olinadi.
    """
    return tuple(round(c + (255 - c) * amount) for c in accent)

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
]


def _font(size: int, bold: bool = False):
    from pathlib import Path
    from PIL import ImageFont

    names = sorted(_FONT_CANDIDATES, key=lambda p: "Bold" not in p) if bold \
        else _FONT_CANDIDATES
    for path in names:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default(size)


def _gradient(draw, top: tuple, bottom: tuple) -> None:
    for y in range(H):
        t = y / H
        draw.line([(0, y), (W, y)],
                  fill=tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))


def _glow(image, accent: tuple) -> None:
    """Yuqori o'ng burchakka urg'u rangida yumshoq nur qo'yadi.

    Tekis gradient o'zicha quruq ko'rinardi. Nur kartaga chuqurlik beradi va
    ko'zni sarlavhaga tortadi. `radial_gradient` tayyor maska beradi —
    piksel-piksel hisoblashdan tez va Pillow'ning o'zida bor.
    """
    from PIL import Image, ImageOps
    size = int(W * 1.9)
    big = ImageOps.invert(Image.radial_gradient("L")).resize((size, size))
    big = big.point(lambda v: int((v / 255) ** 2.4 * 115))      # yumshoq chekka

    # Maskani KESIB olamiz, kichik kvadratni yopishtirmaymiz: kvadrat chetlari
    # kartaning ichiga tushib, ko'zga tashlanadigan vertikal chok qoldirardi.
    # Markaz yuqori-o'ng burchakda; kesilgan maska aynan karta o'lchamida.
    ox, oy = W - size // 2, -size // 2
    mask = big.crop((-ox, -oy, -ox + W, -oy + H))
    image.paste(Image.new("RGB", (W, H), accent), (0, 0), mask)


def _grid(draw, accent: tuple) -> None:
    """Pastki qismga sezilar-sezilmas gorizontal panjara — mato tuyg'usi."""
    for i in range(9):
        y = H - 300 + i * 34
        draw.line([(0, y), (W, y)], fill=accent + (7,), width=1)


# ─── matnni kartaga sig'dirish ────────────────────────────────────────────────

def _wrap(draw, text: str, font, width: int) -> list[str]:
    """Matnni berilgan kenglikka so'z chegarasi bo'yicha bo'ladi."""
    lines: list[str] = []
    line = ""
    for word in text.split():
        probe = f"{line} {word}".strip()
        if draw.textlength(probe, font=font) <= width or not line:
            line = probe
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _fit(draw, text: str, sizes: list[int], width: int, max_lines: int, bold: bool):
    """Sarlavhani sig'adigan ENG KATTA o'lchamda qaytaradi.

    Sarlavha uzunligi oldindan noma'lum (modeldan keladi), shuning uchun bitta
    qat'iy kegl ishlamaydi: qisqa sarlavha kichkina bo'lib qolardi, uzuni esa
    kartadan toshib ketardi. Kattadan kichikka qarab sinaladi.
    """
    for size in sizes:
        font = _font(size, bold=bold)
        lines = _wrap(draw, text, font, width)
        if len(lines) <= max_lines:
            return font, lines
    font = _font(sizes[-1], bold=bold)
    lines = _wrap(draw, text, font, width)[:max_lines]
    if lines:
        lines[-1] = lines[-1].rstrip(" .,;:") + "…"
    return font, lines


# ─── post matnidan sarlavha va asosiy fikrni ajratish ─────────────────────────

_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF←-⇿⌀-➿️‍]+"
)


def _clean(text: str) -> str:
    """Markdown, HTML, emoji va hashtaglarni olib tashlaydi — kartaga toza matn."""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"[*_`#]", "", text)
    text = _EMOJI_RE.sub("", text)
    return " ".join(text.split()).strip(" —-–:·")


def extract(text: str) -> tuple[str, str]:
    """Post matnidan `(sarlavha, ost-sarlavha)` ajratadi.

    Sarlavha — birinchi **qalin** qator (barcha generatorlar postni shunday
    boshlaydi); topilmasa birinchi mazmunli qator.

    Ost-sarlavha — birinchi `> ` sitatasi. Bu tasodifiy tanlov emas: muharrir
    qatlami (`critique_and_improve`) postning ENG muhim fikrini aynan sitataga
    chiqaradi, ya'ni karta uchun tayyor asosiy g'oya.
    """
    lines = [ln.strip() for ln in (text or "").split("\n")]

    title = ""
    for line in lines:
        if not line:
            continue
        bold = re.search(r"\*\*(.+?)\*\*", line)
        title = _clean(bold.group(1) if bold else line)
        if title:
            break

    subtitle = ""
    for line in lines:
        if re.match(r"^\s*(?:&gt;|\\>|>)\s?", line):
            candidate = _clean(re.sub(r"^\s*(?:&gt;|\\>|>)\s?", "", line))
            # Sarlavhani takrorlagan sitata kartada ikki marta chiqmasin
            if candidate and candidate.lower() != title.lower():
                subtitle = candidate
                break

    return title, subtitle


# ─── post turi belgisi (chiziladi, emoji emas) ────────────────────────────────

def _mark(draw, kind: str, cx: int, cy: int, accent: tuple) -> None:
    """Post turini bildiruvchi kichik geometrik belgi."""
    r = 13
    if kind == "educational":                     # kitob — ikki sahifa
        draw.polygon([(cx - r, cy + r), (cx - r, cy - r + 4), (cx - 1, cy - r),
                      (cx - 1, cy + r - 3)], fill=accent)
        draw.polygon([(cx + r, cy + r), (cx + r, cy - r + 4), (cx + 1, cy - r),
                      (cx + 1, cy + r - 3)], fill=accent)
    elif kind == "practical":                     # belgi — bajarildi
        draw.line([(cx - r, cy), (cx - 3, cy + r - 3)], fill=accent, width=5)
        draw.line([(cx - 3, cy + r - 3), (cx + r, cy - r + 2)], fill=accent, width=5)
    elif kind == "tool":                          # asbob — gayka
        draw.regular_polygon((cx, cy, r), 6, rotation=90, outline=accent, width=5)
        draw.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=accent)
    elif kind == "news":                          # signal — tarqalayotgan to'lqin
        draw.ellipse([cx - 4, cy + 4, cx + 4, cy + 12], fill=accent)
        for i in (1, 2):
            box = [cx - 6 * i - 2, cy - 6 * i + 4, cx + 6 * i + 2, cy + 6 * i + 12]
            draw.arc(box, start=225, end=315, fill=accent, width=4)
    elif kind == "digest":                        # ustunlar — hafta yakuni
        for i, h in enumerate((10, 20, 15)):
            x = cx - r + i * 11
            draw.rectangle([x, cy + r - h, x + 7, cy + r], fill=accent)
    else:                                          # nuqta
        draw.ellipse([cx - 7, cy - 7, cx + 7, cy + 7], fill=accent)


# ─── karta ────────────────────────────────────────────────────────────────────

def render(post_type: str, title: str, subtitle: str = "", date_text: str = "") -> bytes | None:
    """Muqova kartasini PNG baytlari sifatida qaytaradi (xato bo'lsa None)."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        logger.warning("Pillow o'rnatilmagan — muqova kartasi chizilmaydi")
        return None

    title = (title or "").strip()
    if not title:
        logger.warning("Sarlavhasiz post — muqova kartasi chizilmadi")
        return None

    label, bg_top, bg_bottom, accent = STYLES.get(post_type, FALLBACK)

    try:
        image = Image.new("RGB", (W, H), bg_top)
        draw = ImageDraw.Draw(image, "RGBA")
        _gradient(draw, bg_top, bg_bottom)
        _glow(image, accent)
        draw = ImageDraw.Draw(image, "RGBA")     # paste'dan keyin qayta bog'lanadi
        _grid(draw, accent)

        inner = W - MARGIN * 2

        # ── yuqori qator: tabletka-yorliq ──
        f_label = _font(27, bold=True)
        spaced = " ".join(label)
        tw = draw.textlength(spaced, font=f_label)
        pill_w = int(tw) + 96
        draw.rounded_rectangle([MARGIN, MARGIN, MARGIN + pill_w, MARGIN + 58],
                               radius=29, fill=accent + (26,), outline=accent + (90,), width=2)
        _mark(draw, post_type, MARGIN + 34, MARGIN + 29, accent)
        draw.text((MARGIN + 60, MARGIN + 15), spaced, font=f_label, fill=accent)

        # ── sarlavha ──
        f_title, title_lines = _fit(
            draw, title, [88, 78, 68, 60, 52, 46], inner, max_lines=5, bold=True
        )
        line_h = int(f_title.size * 1.2)
        block_h = len(title_lines) * line_h

        sub_lines: list[str] = []
        f_sub = None
        if subtitle:
            f_sub, sub_lines = _fit(
                draw, subtitle, [38, 34, 31, 28], inner, max_lines=3, bold=False
            )
            block_h += 52 + len(sub_lines) * int(f_sub.size * 1.36)

        # Blok sarlavha va pastki qator orasida optik markazda
        band_top, band_bottom = MARGIN + 58, H - 150
        y = max(band_top + 60, band_top + (band_bottom - band_top - block_h) // 2)

        # Sarlavha yonidagi vertikal urg'u chizig'i
        draw.rounded_rectangle(
            [MARGIN - 28, y + 8, MARGIN - 21, y + block_h - 8], radius=4, fill=accent
        )

        for line in title_lines:
            draw.text((MARGIN, y), line, font=f_title, fill=INK)
            y += line_h

        if sub_lines and f_sub:
            y += 30
            sub_ink = _tint(accent)
            for line in sub_lines:
                draw.text((MARGIN, y), line, font=f_sub, fill=sub_ink)
                y += int(f_sub.size * 1.36)

        # ── pastki qator ──
        f_foot = _font(29)
        draw.line([MARGIN, H - 118, W - MARGIN, H - 118], fill=DIVIDER, width=2)
        draw.ellipse([MARGIN, H - 88, MARGIN + 12, H - 76], fill=accent)
        foot_ink = _tint(accent, 0.25)
        draw.text((MARGIN + 26, H - 92), CHANNEL, font=f_foot, fill=foot_ink)
        if date_text:
            dw = draw.textlength(date_text, font=f_foot)
            draw.text((W - MARGIN - dw, H - 92), date_text, font=f_foot, fill=foot_ink)

        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()
    except Exception as e:
        logger.error(f"Muqova kartasini chizishda xato: {e}")
        return None


def render_for_post(text: str, post_type: str, date_text: str = "") -> bytes | None:
    """Post matnidan to'g'ridan-to'g'ri muqova kartasi chizadi."""
    title, subtitle = extract(text)
    return render(post_type, title, subtitle, date_text)
