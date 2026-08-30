"""Ikkinchi miya — so'nish modeli va eslatma oqimi (Faza 01).

Eng muhim shart: TOPILISH teginish hisoblanmaydi. Aks holda har qidiruv
butun bazani "yangilab", so'nishni bekor qilardi va baza yana axlatxonaga
aylanardi — bu esa loyihaning butun ma'nosini yo'qotadi.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services import notes
from app.services.notes import note_service, retention, strength, tier

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def _ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


# ─── so'nish matematikasi ──────────────────────────────────────────────────────

def test_fresh_note_is_active():
    assert tier("fikr", 1, _ago(0), now=NOW) == "active"


@pytest.mark.parametrize("days,expected", [
    (5, "active"), (15, "warm"), (40, "cold"), (90, "archive"),
])
def test_note_fades_through_the_tiers(days, expected):
    assert tier("fikr", 1, _ago(days), now=NOW) == expected


def test_use_slows_the_fading():
    """Bir marta ishlatilgan fikr arxivga tushadi, ko'p ishlatilgani turadi."""
    once = tier("fikr", 1, _ago(90), now=NOW)
    often = tier("fikr", 20, _ago(90), now=NOW)
    assert once == "archive"
    assert often in ("warm", "cold"), "ishlatilgan eslatma uzoqroq turishi kerak"


def test_strength_grows_with_use_but_not_linearly():
    """ln() — birinchi ishlatilishlar ko'proq beradi, keyin sekinlashadi."""
    s1, s5, s20 = (strength("fikr", n) for n in (1, 5, 20))
    assert s1 < s5 < s20
    assert (s5 - s1) > (s20 - s5), "o'sish sekinlashishi kerak"


def test_person_outlives_a_passing_thought():
    """Odam haqidagi ma'lumot bir martalik fikrdan uzoq kerak bo'ladi."""
    assert strength("shaxs", 1) > strength("fikr", 1)
    # 90 kundan keyin: fikr arxivda, odam haqidagi ma'lumot hamon topiladi
    assert tier("fikr", 1, _ago(90), now=NOW) == "archive"
    assert tier("shaxs", 1, _ago(90), now=NOW) != "archive"


def test_pinned_never_fades():
    assert tier("fikr", 0, _ago(9999), pinned=True, now=NOW) == "core"


def test_retention_is_bounded():
    assert 0 < retention("fikr", 1, _ago(1000), now=NOW) < 0.01
    assert retention("fikr", 1, NOW, now=NOW) == pytest.approx(1.0)


def test_unknown_kind_falls_back_to_default():
    assert strength("nomalum", 1) == notes.DEFAULT_BASE_DAYS


def test_naive_timestamp_is_handled():
    """DB dan tz-siz sana kelsa ham yiqilmasligi kerak."""
    naive = datetime(2026, 8, 25, 12, 0)
    assert tier("fikr", 1, naive, now=NOW) in ("active", "warm", "cold", "archive")


# ─── saqlash ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_empty_note_is_not_saved():
    assert await note_service.save("   ") is None


@pytest.mark.asyncio
async def test_describe_falls_back_when_llm_fails():
    """LLM yiqilsa ham eslatma saqlanishi kerak — xom matn yo'qolmasin."""
    with patch("app.ai.agents.base_agent.BaseAgent._call_llm",
               AsyncMock(side_effect=RuntimeError("LLM"))):
        meta = await note_service._describe("Birinchi qator\nikkinchi qator")
    assert meta["title"] == "Birinchi qator"
    assert meta["kind"] == "fikr"


@pytest.mark.asyncio
async def test_describe_rejects_unknown_kind():
    with patch("app.ai.agents.base_agent.BaseAgent._call_llm",
               AsyncMock(return_value='{"title":"Test","kind":"qandaydir","summary":"x"}')):
        meta = await note_service._describe("matn")
    assert meta["kind"] == "fikr", "noma'lum tur standartga tushishi kerak"


@pytest.mark.asyncio
async def test_describe_uses_llm_result():
    with patch("app.ai.agents.base_agent.BaseAgent._call_llm",
               AsyncMock(return_value='{"title":"RAG haqida","kind":"maqola","summary":"Qisqacha"}')):
        meta = await note_service._describe("uzun matn")
    assert meta == {"title": "RAG haqida", "kind": "maqola", "summary": "Qisqacha"}


# ─── qidirish teginish EMAS ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_does_not_count_as_a_touch():
    """Bu shu faylning eng muhim testi — qarang: modul izohi."""
    chroma = {
        "metadatas": [[{"note_id": 1, "title": "T"}]],
        "distances": [[0.3]],
    }
    note = SimpleNamespace(id=1, title="T", kind="fikr", summary="s", body="b",
                           created_at=NOW, access_count=3,
                           last_touched=_ago(1), pinned=False)

    class _Res:
        def scalars(self): return self
        def all(self): return [note]

    class _DB:
        async def __aenter__(self): return self
        async def __aexit__(self, *e): return False
        async def execute(self, *a): return _Res()

    with patch("app.ai.vector_db.chroma_client.chroma_client.query",
               AsyncMock(return_value=chroma)), \
         patch("app.ai.rag.embedder.get_embedder") as emb, \
         patch("app.database.session.AsyncSessionLocal", _DB), \
         patch.object(note_service, "touch",
                      AsyncMock(side_effect=AssertionError("qidiruv teginish EMAS"))):
        emb.return_value.embed_one.return_value = [0.1]
        found = await note_service.search("savol")

    assert len(found) == 1
    assert found[0]["similarity"] == 0.7
    assert found[0]["tier"]


@pytest.mark.asyncio
async def test_search_survives_chroma_failure():
    with patch("app.ai.vector_db.chroma_client.chroma_client.query",
               AsyncMock(side_effect=RuntimeError("chroma"))), \
         patch("app.ai.rag.embedder.get_embedder"):
        assert await note_service.search("savol") == []


# ─── qaytarish ─────────────────────────────────────────────────────────────────

def _note(nid, kind, days, count=1, pinned=False):
    return SimpleNamespace(id=nid, title=f"N{nid}", kind=kind, summary=f"s{nid}",
                           body="b", created_at=NOW, access_count=count,
                           last_touched=_ago(days), pinned=pinned)


@pytest.mark.asyncio
async def test_resurface_mixes_active_with_something_archived():
    """Arxivdan tasodifiy qaytarish — miyani arxivdan ajratib turadigan narsa."""
    rows = [_note(1, "fikr", 1), _note(2, "fikr", 2), _note(3, "fikr", 300),
            _note(4, "fikr", 400)]

    class _Res:
        def scalars(self): return self
        def all(self): return rows

    class _DB:
        async def __aenter__(self): return self
        async def __aexit__(self, *e): return False
        async def execute(self, *a): return _Res()

    with patch("app.database.session.AsyncSessionLocal", _DB):
        picked = await note_service.resurface(active=2, archived=1)

    tiers = [p["tier"] for p in picked]
    assert tiers.count("active") == 2
    assert "archive" in tiers, "arxivdan bittasi qaytishi kerak"


@pytest.mark.asyncio
async def test_resurface_survives_empty_database():
    class _Res:
        def scalars(self): return self
        def all(self): return []

    class _DB:
        async def __aenter__(self): return self
        async def __aexit__(self, *e): return False
        async def execute(self, *a): return _Res()

    with patch("app.database.session.AsyncSessionLocal", _DB):
        assert await note_service.resurface() == []


# ─── bot bilan ulanish ─────────────────────────────────────────────────────────

def test_plain_text_is_routed_to_notes():
    """Holatsiz matn menyu emas, ESLATMA bo'lishi kerak — asosiy kirish nuqtasi."""
    import inspect
    from app.services.bot_service import BotService

    source = inspect.getsource(BotService._handle_message)
    assert "_save_note" in source
    # Eski xulq (menyu qaytarish) o'rnini eslatma egallagan
    marker = source.index("if not state:")
    assert "_save_note" in source[marker:marker + 400]


def test_briefing_includes_resurfaced_notes():
    import inspect
    from app.services.bot_service import BotService

    assert "_resurfaced_block" in inspect.getsource(BotService.send_morning_reminder)
