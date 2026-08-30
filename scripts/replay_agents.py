"""Tarixiy xabarlarni agentlardan qayta o'tkazib, xarajat o'lchovini to'ldiradi.

Nima uchun: `AgentLog` faqat jonli trafik bilan to'ladi, ya'ni haqiqiy
taqsimotni ko'rish uchun haftalar kerak. `messages` jadvalida esa oylar
davomida yig'ilgan HAQIQIY foydalanuvchi xabarlari yotibdi. Ularni bir marta
qayta yurgizsak, bir soatda haqiqiy manzara chiqadi: qaysi agent qancha
turadi, necha foiz xabar qimmat yo'lga tushadi, kechikish qanday.

XAVFSIZLIK — bu skript hech kimga xabar YUBORMAYDI:
  * `ai_service.process_message` CHAQIRILMAYDI. U DB ga yozadi, foydalanuvchi
    statistikasini o'zgartiradi, egaga bildirishnoma yuboradi va fon
    vazifalarini boshlaydi.
  * Agentlar to'g'ridan-to'g'ri chaqiriladi — ular sof funksiyalar, yagona
    nojo'ya ta'siri `AgentLog` ga yozish.
  * Barcha yozuvlar `replay.` prefiksi va `extra.synthetic = true` bilan
    belgilanadi, shunda dashboard ularni haqiqiy trafikdan ajratadi.

Ishlatish:

    # avval nima bo'lishini ko'rish (API chaqirilmaydi, pul ketmaydi)
    uv run python scripts/replay_agents.py --dry-run

    # 300 ta xabar tahlil+klassifikatsiyadan, 30 tasi javob generatsiyasidan
    uv run python scripts/replay_agents.py --limit 300 --responses 30

    # tasdiqlashsiz (avtomatlashtirilgan yurgizish uchun)
    uv run python scripts/replay_agents.py --limit 300 --yes
"""
import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

# Qayta ishlanmaydigan o'rinbosar matnlar — ular haqiqiy xabar emas
_PLACEHOLDERS = (
    "[MAXFIY MA'LUMOT",
    "[GUARDRAIL:",
    "📎 Hujjat yuborildi",
    "📷 Rasm yuborildi",
    "🎤 Ovozli xabar yuborildi",
    "🎥 Video yuborildi",
    "🎭 Sticker yuborildi",
    "📁 Fayl yuborildi",
)

MIN_LENGTH = 10          # juda qisqa xabar o'lchov uchun ma'lumot bermaydi
CONCURRENCY = 4          # API ni bo'g'ib qo'ymaslik uchun


async def load_messages(limit: int) -> list[str]:
    """Tarixiy foydalanuvchi xabarlarini oladi (takrorlanmaganlari)."""
    from sqlalchemy import select, desc
    from app.database.models import Message, MessageRole
    from app.database.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Message.content)
            .where(Message.role == MessageRole.USER)
            .order_by(desc(Message.created_at))
            .limit(limit * 4)  # filtrdan keyin yetarli qolishi uchun zaxira
        )
        rows = [r[0] for r in result.all()]

    seen: set[str] = set()
    texts: list[str] = []
    for text in rows:
        text = (text or "").strip()
        if len(text) < MIN_LENGTH:
            continue
        if any(text.startswith(p) for p in _PLACEHOLDERS):
            continue
        key = text.lower()[:120]
        if key in seen:
            continue
        seen.add(key)
        texts.append(text)
        if len(texts) >= limit:
            break
    return texts


def estimate_cost(count: int, responses: int) -> float:
    """Taxminiy narx — foydalanuvchi tasdiqlashidan oldin ko'rsatiladi.

    Taxmin real chaqiruvlar o'rniga o'lchamlarga asoslanadi: tahlil va
    klassifikatsiya ~600 kiruvchi / ~150 chiquvchi token (Haiku), javob
    generatsiyasi ~1200 / ~250 (asosiy model).
    """
    from app.ai.models import estimate_cost_usd
    from app.config import settings

    fast = settings.model_for_tier("fast")
    balanced = settings.model_for_tier("balanced")

    per_fast = estimate_cost_usd(fast, input_tokens=600, output_tokens=150) or 0.0
    per_reply = estimate_cost_usd(balanced, input_tokens=1200, output_tokens=250) or 0.0
    return count * 2 * per_fast + responses * per_reply


async def replay_one(text: str, semaphore: asyncio.Semaphore) -> dict | None:
    """Bitta xabarni tahlil va klassifikatsiyadan o'tkazadi."""
    from app.ai.agents.analysis_agent import analysis_agent
    from app.ai.agents.classifier_agent import classifier_agent

    async with semaphore:
        try:
            analysis, classification = await asyncio.gather(
                analysis_agent.analyze_message(text),
                classifier_agent.classify(text),
            )
        except Exception as e:
            print(f"  ⚠️  xato: {type(e).__name__}: {e}")
            return None

    return {
        "category": classification.category.value,
        "language": classification.language,
        "confidence": analysis.confidence,
        "spam": analysis.is_spam,
        "notify": classification.should_notify_owner,
    }


async def replay_response(text: str, semaphore: asyncio.Semaphore) -> bool:
    """Javob generatsiyasi — qimmat yo'lning haqiqiy narxini o'lchash uchun."""
    from app.ai.agents.response_agent import response_agent
    from app.database.models import TelegramUser

    # DB ga yozilmaydigan vaqtinchalik obyekt — faqat prompt qurish uchun
    user = TelegramUser(telegram_id=0, first_name="O'lchov")
    async with semaphore:
        try:
            await response_agent.generate_response(
                user=user, current_message=text, history=[],
                relationship_type="stranger",
            )
            return True
        except Exception as e:
            print(f"  ⚠️  javob xatosi: {type(e).__name__}: {e}")
            return False


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=200,
                        help="tahlil+klassifikatsiyadan o'tkaziladigan xabarlar (default 200)")
    parser.add_argument("--responses", type=int, default=20,
                        help="javob generatsiyasidan o'tkaziladigan namuna (default 20)")
    parser.add_argument("--dry-run", action="store_true",
                        help="API chaqirmasdan faqat rejani ko'rsatish")
    parser.add_argument("--yes", action="store_true", help="tasdiqlashni so'ramaslik")
    args = parser.parse_args()

    from app.ai import usage_log
    from app.config import settings

    print("Tarixiy xabarlar o'qilmoqda...")
    texts = await load_messages(args.limit)
    if not texts:
        print("❌ Yaroqli xabar topilmadi. DB bo'shmi yoki hammasi o'rinbosarmi?")
        return 1

    responses = min(args.responses, len(texts))
    cost = estimate_cost(len(texts), responses)

    print(f"\n{'─' * 62}")
    print(f"  Xabarlar          : {len(texts)} ta (takrorlanmagan, haqiqiy)")
    print(f"  Tahlil + klassif. : {len(texts) * 2} chaqiruv → {settings.model_for_tier('fast')}")
    print(f"  Javob namunasi    : {responses} chaqiruv → {settings.model_for_tier('balanced')}")
    print(f"  Taxminiy narx     : ${cost:.2f}")
    print(f"  Belgilanishi      : replay.* (haqiqiy trafikka aralashmaydi)")
    print(f"{'─' * 62}")

    if args.dry_run:
        print("\n(--dry-run — hech narsa chaqirilmadi)")
        print("Namuna xabarlar:")
        for t in texts[:5]:
            print(f"  · {t[:70]}")
        return 0

    if not args.yes:
        answer = input("\nDavom etamizmi? [ha/yo'q]: ").strip().lower()
        if answer not in ("ha", "h", "yes", "y"):
            print("Bekor qilindi.")
            return 0

    semaphore = asyncio.Semaphore(CONCURRENCY)
    print(f"\nYurgizilmoqda ({CONCURRENCY} parallel)...")

    # `synthetic_run` ichidagi HAR BIR chaqiruv replay deb belgilanadi
    with usage_log.synthetic_run():
        results = await asyncio.gather(
            *(replay_one(t, semaphore) for t in texts)
        )
        if responses:
            print(f"Javob namunasi ({responses} ta)...")
            await asyncio.gather(
                *(replay_response(t, semaphore) for t in texts[:responses])
            )

    # Fon vazifalari (AgentLog yozuvi) tugashini kutamiz
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    ok = [r for r in results if r]
    print(f"\n{'─' * 62}")
    print(f"  Muvaffaqiyatli: {len(ok)}/{len(texts)}")

    if ok:
        categories = Counter(r["category"] for r in ok)
        languages = Counter(r["language"] for r in ok)
        low_conf = sum(1 for r in ok if r["confidence"] < settings.confidence_threshold)
        notify = sum(1 for r in ok if r["notify"])

        print("\n  Kategoriya taqsimoti:")
        for name, n in categories.most_common():
            print(f"    {name:12} {n:4}  ({n / len(ok) * 100:.0f}%)")
        print("\n  Til taqsimoti:")
        for name, n in languages.most_common():
            print(f"    {name:12} {n:4}  ({n / len(ok) * 100:.0f}%)")
        print(f"\n  Egaga yo'naltiriladi : {notify} ({notify / len(ok) * 100:.0f}%)")
        print(f"  Ishonch chegarasidan past: {low_conf} ({low_conf / len(ok) * 100:.0f}%)")

    print(f"{'─' * 62}")
    print("\nHaqiqiy narx va kechikish dashboardda: /dashboard/costs → «Replay»")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    raise SystemExit(asyncio.run(main()))
