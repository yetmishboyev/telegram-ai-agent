"""O'zbek matni bilan ishlash yordamchilari.

`to_latin_uz` — matndagi krill harflarni o'zbek lotin alifbosiga o'giradi.
LLM ba'zan lotincha o'zbek matniga krill harflarni aralashtirib yuboradi
(masalan "qidirади"); kanal postlari faqat lotin bo'lgani uchun har qanday
krill belgi xato hisoblanadi va deterministik ravishda tuzatiladi.
"""

# Krill (o'zbek/rus) → o'zbek lotin. Ko'p harfli natijalar (sh, ch, yo...) qo'llab-quvvatlanadi.
_CYR_LAT: dict[str, str] = {
    "а": "a", "б": "b", "в": "v", "г": "g", "ғ": "g'", "д": "d", "е": "e",
    "ё": "yo", "ж": "j", "з": "z", "и": "i", "й": "y", "к": "k", "қ": "q",
    "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s",
    "т": "t", "у": "u", "ў": "o'", "ф": "f", "х": "x", "ҳ": "h", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "'", "ь": "", "э": "e", "ю": "yu",
    "я": "ya", "ы": "i",
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Ғ": "G'", "Д": "D", "Е": "E",
    "Ё": "Yo", "Ж": "J", "З": "Z", "И": "I", "Й": "Y", "К": "K", "Қ": "Q",
    "Л": "L", "М": "M", "Н": "N", "О": "O", "П": "P", "Р": "R", "С": "S",
    "Т": "T", "У": "U", "Ў": "O'", "Ф": "F", "Х": "X", "Ҳ": "H", "Ц": "Ts",
    "Ч": "Ch", "Ш": "Sh", "Щ": "Shch", "Ъ": "'", "Ь": "", "Э": "E", "Ю": "Yu",
    "Я": "Ya", "Ы": "I",
}


def to_latin_uz(text: str) -> str:
    """Matndagi krill belgilarni o'zbek lotiniga o'giradi, qolganini tegmaydi."""
    if not text:
        return text
    return "".join(_CYR_LAT.get(ch, ch) for ch in text)
