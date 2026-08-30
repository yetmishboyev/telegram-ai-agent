"""Ish o'rni so'rovini aniqlash.

Agent HECH QACHON o'zidan CV, obyektivka yoki shaxsiy ma'lumot so'ramaydi —
yagona istisno shu yerda: foydalanuvchi bo'sh ish o'rni yoki ishga kirish
haqida so'raganda. Faqat o'shanda hujjat so'raladi va murojaat egaga
yo'naltiriladi.

Aniqlash deterministik (regex): LLM ga tayanib qolmaslik uchun. Prompt
qatlamidagi taqiq (`AGENT_PERSONA`, `ESCALATION_PROMPT`) esa qolgan barcha
holatlarda hujjat so'ralishining oldini oladi.
"""
import re

# O'zbek apostrofi turli belgilar bilan yoziladi (o'rin / oʻrin / o`rin / o’rin)
_A = r"['ʻ`’‘]?"

_JOB_PATTERNS: list[re.Pattern] = [
    # ─── o'zbekcha ──────────────────────────────────────────────────────────
    # "bo'sh ish o'rni bormi", "bo'sh o'rin bormi", "bo'sh joy bormi"
    re.compile(rf"bo{_A}sh\s+(?:ish\s+)?(?:o{_A}rin|o{_A}rn|joy)", re.I),
    re.compile(r"\bvakansiya", re.I),
    re.compile(rf"\bish\s+o{_A}rn", re.I),                     # ish o'rni
    # "ishga kirsam bo'ladimi", "ishga olasizmi", "ishga qabul qilasizmi"
    re.compile(
        r"\bishga\s+(?:kirsam|kirmoqchi|kiraman|kira\b|kirsak|kirish\b"
        r"|ol(?:asiz|asizmi|asizlarmi|sangiz|ishadi)?\b|qabul|joylash|yollay)",
        re.I,
    ),
    # Faqat SO'ROQ shakli: "ish bormi". Yalang'och "ish bor" TAKLIF bo'ladi
    # ("siz uchun ish bor") va u pastdagi darvozada rad etiladi.
    re.compile(r"\bish\s+bor(?:mi|ma)\b", re.I),
    re.compile(rf"\bish\s+(?:qidir|izla|topmoqchi|so{_A}rab)", re.I),
    re.compile(r"\bishlamoqchi", re.I),                         # "sizda ishlamoqchiman"
    re.compile(r"\b[xh]odim\s+(?:kerak|qidir|izla|olasiz)", re.I),
    re.compile(rf"\b(?:staj|amaliyot\s+o{_A}t)", re.I),
    # Foydalanuvchining o'zi hujjat taklif qilsa ham — bu ish murojaati
    re.compile(rf"\b(?:cv|rezyume|obyektivka)\s*(?:mni|imni|ni)?\s*"
               rf"(?:yubor|tashla|jo{_A}nat|bersam|beray)", re.I),

    # ─── ruscha ─────────────────────────────────────────────────────────────
    re.compile(r"ваканси", re.I),
    re.compile(r"есть\s+ли\s+работа|работа\s+есть|ищу\s+работу", re.I),
    re.compile(r"устроит(?:ь|)ся\s+на\s+работу|приём\s+на\s+работу|прием\s+на\s+работу", re.I),
    re.compile(r"стажиров|требуются\s+сотрудник|набор\s+сотрудник", re.I),
    re.compile(r"резюме\s+(?:отправ|скинуть|прислать)", re.I),

    # ─── inglizcha ──────────────────────────────────────────────────────────
    re.compile(r"\bvacanc(?:y|ies)\b", re.I),
    re.compile(r"\bjob\s+(?:opening|opportunit|offer|vacanc|application)", re.I),
    re.compile(r"\b(?:are\s+you|you\s+are)\s+hiring\b", re.I),
    re.compile(r"\bany\s+openings?\b", re.I),
    re.compile(r"\bapply\s+for\s+(?:a\s+)?(?:job|position)", re.I),
    re.compile(r"\binternship\b", re.I),
    re.compile(r"\bposition\s+available\b", re.I),
]

# ─── teskari yo'nalish: kimdir EGAGA ish taklif qilyapti ──────────────────────
# "Sizda ish bormi" (so'rov) va "Siz uchun ish bor" (taklif) bir xil so'zlardan
# tuzilgan. Ikkinchisi — muhim murojaat va u eskalatsiyaga tushishi kerak, CV
# so'ralishi emas. Shuning uchun taklif belgilari birinchi tekshiriladi.
_JOB_OFFER_PATTERNS: list[re.Pattern] = [
    # uz — ikkinchi shaxsga qaratilgan darak gap
    re.compile(rf"\bsiz(?:ga|ni|lar(?:ga|ni)?)?\s+(?:uchun\s+)?"
               rf"(?:ish|vakansiya|lavozim|o{_A}rin)", re.I),
    re.compile(r"\bish(?:ga)?\s+taklif", re.I),
    re.compile(r"\btaklif\s+(?:qil|et)(?:amiz|moqchimiz|yapmiz)", re.I),
    re.compile(r"\b(?:qabul|ol|yolla)(?:moqchimiz|amiz|ishni\s+xohlaymiz)", re.I),
    re.compile(r"\bbizga\s+(?:kerak|kelib)", re.I),
    # ru
    re.compile(r"предлага(?:ем|ю)\s+(?:вам\s+)?(?:работу|вакансию|должность)", re.I),
    re.compile(r"приглаша(?:ем|ю)\s+вас", re.I),
    re.compile(r"вакансия\s+для\s+вас", re.I),
    # en
    re.compile(r"\b(?:we|I)(?:'d| would)?\s+(?:like\s+to\s+)?offer\s+you\b", re.I),
    re.compile(r"\bjob\s+offer\s+for\s+you\b", re.I),
    re.compile(r"\bwe\s+(?:are|'re)\s+hiring\s+you\b", re.I),
]

# Ish so'roviga javob: murojaat egaga yetkaziladi va SHU YERDA (faqat shu yerda)
# hujjat so'raladi. Va'da berilmaydi — ega hech kimni ishga olishga majbur emas,
# agent esa vakansiya bor-yo'qligini bilmaydi.
#
# MUHIM: hujjat FAYL sifatida so'raladi, matn sifatida emas. Obyektivka matni
# deyarli doim pasport raqamini o'z ichiga oladi va u maxfiy ma'lumot filtriga
# tushib, "ulashmang" javobini olardi — ya'ni agent hujjat so'rab, keyin uni
# jimgina tashlab yuborardi. Fayl esa DOCUMENT tarmog'idan o'tadi va ega
# bildirishnoma oladi.
JOB_INQUIRY_REPLIES: dict[str, str] = {
    "uz": (
        "Ish bo'yicha murojaatingizni Shaxzodbek Yetmishboyevga yetkazaman. "
        "Iltimos, CV yoki obyektivkangizni FAYL (PDF yoki Word) ko'rinishida "
        "yuboring — Shaxzodbek ko'rib chiqib, o'zi javob beradi."
    ),
    "ru": (
        "Ваш вопрос по работе я передам Шахзодбеку Йетмишбоеву. "
        "Пожалуйста, отправьте резюме ФАЙЛОМ (PDF или Word), а не текстом — "
        "он ознакомится и ответит вам сам."
    ),
    "en": (
        "I'll pass your job inquiry on to Shaxzodbek Yetmishboyev. "
        "Please send your CV as a FILE (PDF or Word) rather than as text — "
        "he will review it and reply to you himself."
    ),
}


def is_job_offer(text: str) -> bool:
    """Kimdir EGAGA ish taklif qilyaptimi (so'rov emas)."""
    return any(pattern.search(text) for pattern in _JOB_OFFER_PATTERNS)


def is_job_inquiry(text: str) -> bool:
    """Xabar bo'sh ish o'rni / ishga kirish haqidami.

    Taklif (kimdir egaga ish taklif qilishi) so'rov HISOBLANMAYDI — u
    eskalatsiyaga tushib, egaga yo'naltirilishi kerak.
    """
    if is_job_offer(text):
        return False
    return any(pattern.search(text) for pattern in _JOB_PATTERNS)


def get_job_inquiry_reply(lang: str) -> str:
    return JOB_INQUIRY_REPLIES.get(lang, JOB_INQUIRY_REPLIES["uz"])
