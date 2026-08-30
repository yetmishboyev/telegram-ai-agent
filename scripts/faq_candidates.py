"""Egaga yo'naltirilayotgan xabarlardan FAQ nomzodlarini chiqaradi.

Replay ko'rsatdi: xabarlarning ~18% i egaga yo'naltirilyapti. Ya'ni har
beshinchi odam javob o'rniga "yetkazaman" oladi va javobni ega qo'lda
yozadi. Agent aynan shu ishni kamaytirish uchun qurilgan.

Bu skript tarixiy xabarlarni klassifikatsiyadan o'tkazadi, IMPORTANT
bo'lganlarini yig'adi va mavzu bo'yicha guruhlab, har guruh uchun FAQ
savoli taklif qiladi. JAVOBLARNI EGA YOZADI — skript javob to'qimaydi,
chunki bilim faqat egada.

Natijani botdagi `/faq` menyusi orqali qo'shasiz.

Ishlatish:
    uv run python scripts/faq_candidates.py --limit 200
"""
import argparse
import asyncio
import sys
from pathlib import Path

CONCURRENCY = 4


async def load_messages(limit: int) -> list[str]:
    from scripts.replay_agents import load_messages as _load
    return await _load(limit)


async def collect_escalated(texts: list[str]) -> list[str]:
    """Klassifikatsiya qilib, egaga yo'naltiriladiganlarini ajratadi."""
    from app.ai.agents.classifier_agent import classifier_agent, MessageCategory

    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def one(text: str):
        async with semaphore:
            try:
                r = await classifier_agent.classify(text)
            except Exception:
                return None
            escalates = (
                r.category == MessageCategory.IMPORTANT or r.should_notify_owner
            )
            return text if escalates else None

    results = await asyncio.gather(*(one(t) for t in texts))
    return [t for t in results if t]


async def cluster_into_faq(messages: list[str]) -> str:
    """Xabarlarni mavzu bo'yicha guruhlab, FAQ savollarini taklif qiladi."""
    from app.ai.agents.base_agent import BaseAgent
    from app.ai.models import ModelTier

    class _Clusterer(BaseAgent):
        tier = ModelTier.DEEP          # sifat muhim, chaqiruv bitta
        async def run(self, *a, **kw):
            return None

    listing = "\n".join(f"{i + 1}. {m[:300]}" for i, m in enumerate(messages))
    prompt = f"""Quyida Shaxzodbek Yetmishboyevning Telegramiga kelgan va AI agent JAVOB BERA OLMAY egaga yo'naltirgan xabarlar ro'yxati.

Vazifang: ularni MAVZU bo'yicha guruhla va har takrorlanadigan mavzu uchun bilim bazasiga (FAQ) qo'shsa bo'ladigan savol taklif qil.

Xabarlar:
{listing}

Qoidalar:
- Faqat KAMIDA IKKI marta uchraydigan mavzularni ol — bir martalik shaxsiy murojaat FAQ ga yaramaydi.
- Har guruh uchun: mavzu nomi, nechta xabar tegishli, va foydalanuvchilar SO'RASHI MUMKIN bo'lgan umumlashtirilgan savol.
- JAVOBNI YOZMA — javobni Shaxzodbekning o'zi yozadi, chunki bilim faqat unda.
- Shaxsiy ma'lumot (ism, telefon, manzil) ni ko'chirma.
- Agar takrorlanadigan mavzu topilmasa, buni ochiq ayt.

Quyidagi formatda yoz:

MAVZU: <nomi> (<n> ta xabar)
  Taklif qilinadigan savol: <savol matni>

Oxirida 1-2 gapda umumiy xulosa: qaysi mavzu eng ko'p uchradi va FAQ shundan boshlansa nima o'zgaradi."""

    return await _Clusterer()._call_llm(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3, max_tokens=2000,
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()

    print("Tarixiy xabarlar o'qilmoqda...")
    texts = await load_messages(args.limit)
    if not texts:
        print("❌ Xabar topilmadi.")
        return 1

    print(f"{len(texts)} ta xabar klassifikatsiyadan o'tkazilmoqda...")
    escalated = await collect_escalated(texts)
    share = len(escalated) / len(texts) * 100
    print(f"\nEgaga yo'naltirilgan: {len(escalated)} ta ({share:.0f}%)")

    if len(escalated) < 3:
        print("Guruhlash uchun juda kam — ko'proq xabar bilan qayta urinib ko'ring.")
        return 0

    print("Mavzular ajratilmoqda...\n")
    report = await cluster_into_faq(escalated)

    print("─" * 70)
    print(report)
    print("─" * 70)
    print("\nJavoblarni o'zingiz yozib, botda: /faq → ➕ Yangi FAQ qo'shish")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    raise SystemExit(asyncio.run(main()))
