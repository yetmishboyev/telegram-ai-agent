"""O'zbek tilidagi transkripsiya aniqligini o'lchash vositasi.

Kod yozishdan oldin o'lchash kerak edi — bu o'sha o'lchov. Haqiqiy ovozli
xabarlarni (Telegramdan yuklab olingan .ogg fayllar) berib, natijani o'z
ko'zingiz bilan baholaysiz. Natija yomon bo'lsa VOICE_ENABLED=false qo'yish
yoki boshqa provayder izlash kerak.

Ishlatish:

    # bitta papkadagi hamma ovozli fayl
    uv run python scripts/check_transcription.py ~/Downloads/voices/

    # yoki alohida fayllar
    uv run python scripts/check_transcription.py a.ogg b.ogg

Telegramdan ovozli xabarni saqlash: xabarni bosib "Save As" (Desktop) yoki
Saqlangan xabarlarga forward qilib, keyin yuklab olish.
"""
import asyncio
import sys
from pathlib import Path

AUDIO_SUFFIXES = {".ogg", ".oga", ".mp3", ".m4a", ".wav", ".webm", ".mp4"}


def collect_files(args: list[str]) -> list[Path]:
    files: list[Path] = []
    for arg in args:
        path = Path(arg).expanduser()
        if path.is_dir():
            files.extend(
                sorted(p for p in path.iterdir() if p.suffix.lower() in AUDIO_SUFFIXES)
            )
        elif path.is_file():
            files.append(path)
        else:
            print(f"⚠️  topilmadi: {path}")
    return files


async def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    files = collect_files(sys.argv[1:])
    if not files:
        print("Ovozli fayl topilmadi.")
        return 1

    from app.ai.transcriber import transcriber
    from app.config import settings

    if not transcriber.is_available():
        print(
            "❌ Transkripsiya o'chiq. Tekshiring:\n"
            f"   VOICE_ENABLED={settings.voice_enabled}\n"
            f"   VOICE_PROVIDER={settings.voice_provider}\n"
            f"   OPENAI_API_KEY {'bor' if settings.openai_api_key else 'YO`Q'}"
        )
        return 1

    print(f"Model: {settings.voice_model} · til: {settings.voice_language}")
    print(f"Fayllar: {len(files)} ta\n" + "─" * 70)

    ok = 0
    for i, path in enumerate(files, 1):
        audio = path.read_bytes()
        size_kb = len(audio) / 1024
        print(f"\n{i}. {path.name}  ({size_kb:.0f} KB)")
        text = await transcriber.transcribe(audio, mime=None)
        if text:
            ok += 1
            print(f"   → {text}")
        else:
            print("   → (natija yo'q)")

    print("\n" + "─" * 70)
    print(f"Natija: {ok}/{len(files)} ta fayl matnga aylandi.")
    print(
        "\nEndi O'ZINGIZ baholang: matnlar tushunarlimi? Ismlar, atamalar to'g'ri\n"
        "yozilganmi? Agar yarmi tushunarsiz bo'lsa — VOICE_ENABLED=false qo'ying\n"
        "va boshqa yechim izlang (lokal model yoki boshqa provayder)."
    )
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    raise SystemExit(asyncio.run(main()))
