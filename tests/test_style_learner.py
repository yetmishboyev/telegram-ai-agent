"""style_learner.learn() — dedup va saqlash-chegarasi testlari (roadmap Faza 3, band 7)."""
import pytest
from unittest.mock import AsyncMock, patch

from app.ai.agents.style_learner import (
    style_learner, _is_learnable,
    MAX_STORED_STYLE_EXAMPLES, MIN_SAMPLES_FOR_CARD, CARD_REFRESH_EVERY,
)


@pytest.mark.asyncio
async def test_learn_ignores_too_short_text():
    with patch("app.ai.agents.style_learner.chroma_client.get", AsyncMock()) as mock_get:
        await style_learner.learn("ok")
    mock_get.assert_not_called()


@pytest.mark.asyncio
async def test_learn_skips_duplicate_text():
    with patch(
        "app.ai.agents.style_learner.chroma_client.get",
        AsyncMock(return_value={
            "ids": ["a"],
            "documents": ["Salom qalaysiz"],
            "metadatas": [{"saved_at": "2026-01-01T00:00:00"}],
        }),
    ), patch("app.ai.agents.style_learner.chroma_client.upsert", AsyncMock()) as mock_upsert, \
       patch("app.ai.agents.style_learner.chroma_client.delete", AsyncMock()), \
       patch.object(style_learner._embedder, "embed_one", return_value=[0.1, 0.2]):
        await style_learner.learn("  Salom qalaysiz  ")

    mock_upsert.assert_not_called()


@pytest.mark.asyncio
async def test_learn_stores_new_unique_text():
    with patch(
        "app.ai.agents.style_learner.chroma_client.get",
        AsyncMock(return_value={"ids": [], "documents": [], "metadatas": []}),
    ), patch("app.ai.agents.style_learner.chroma_client.upsert", AsyncMock()) as mock_upsert, \
       patch("app.ai.agents.style_learner.chroma_client.delete", AsyncMock()) as mock_delete, \
       patch.object(style_learner._embedder, "embed_one", return_value=[0.1, 0.2]):
        await style_learner.learn("Yangi va noyob matn")

    mock_upsert.assert_called_once()
    mock_delete.assert_not_called()


@pytest.mark.asyncio
async def test_learn_enforces_storage_cap_by_deleting_oldest():
    existing_ids = [f"id{i}" for i in range(MAX_STORED_STYLE_EXAMPLES)]
    existing_docs = [f"matn {i}" for i in range(MAX_STORED_STYLE_EXAMPLES)]
    existing_metas = [
        {"saved_at": f"2026-01-01T00:00:{i:02d}"} for i in range(MAX_STORED_STYLE_EXAMPLES)
    ]

    with patch(
        "app.ai.agents.style_learner.chroma_client.get",
        AsyncMock(return_value={
            "ids": existing_ids,
            "documents": existing_docs,
            "metadatas": existing_metas,
        }),
    ), patch("app.ai.agents.style_learner.chroma_client.upsert", AsyncMock()), \
       patch("app.ai.agents.style_learner.chroma_client.delete", AsyncMock()) as mock_delete, \
       patch.object(style_learner, "_maybe_refresh_card", AsyncMock()), \
       patch.object(style_learner._embedder, "embed_one", return_value=[0.1, 0.2]):
        await style_learner.learn("Yana bir yangi matn")

    mock_delete.assert_called_once()
    deleted_ids = mock_delete.call_args.kwargs.get("ids", mock_delete.call_args.args[0] if mock_delete.call_args.args else None)
    assert deleted_ids == ["id0"]  # eng eski (saved_at eng kichik) yozuv o'chiriladi


# ─── sifat darvozasi: qanday xabar namuna bo'la oladi ──────────────────────────

@pytest.mark.parametrize("text", [
    "ok",                                              # juda qisqa
    "ha",
    "/menu",                                           # bot buyrug'i
    "https://example.com/juda/uzun/havola",            # faqat havola
    "Manga hamma bollarni CV sini tashab bera olasizmi?",   # hujjat so'rovi
    "CV larini o'zbek tilida qilib bersin keyin bollarga ayting",
    "Hammani Yangilangan CV simi bu eng oxirgisi",
    "Obyektivkangizni tashlang iltimos",
    "Hujjatlarni ertaga olib keling",
    "Ma'lumotlaringizni yuboring iltimos",             # ma'lumot + so'rov fe'li
])
def test_not_learnable_messages(text):
    assert _is_learnable(text) is False


@pytest.mark.parametrize("text", [
    "Assalomu alaykum, qalaysiz? Ertaga uchrashamizmi",
    "Yaxshi, men buni ko'rib chiqaman va javob beraman",
    "Ma'lumot uchun rahmat, juda foydali bo'ldi",      # "ma'lumot" bor, so'rov yo'q
    "Bugun kechqurun bo'sh bo'laman, qo'ng'iroq qiling",
])
def test_learnable_messages(text):
    assert _is_learnable(text) is True


@pytest.mark.asyncio
async def test_learn_skips_document_request_without_touching_db():
    """Hujjat so'rovi umuman saqlanmaydi — promptga tushish ehtimoli yo'q."""
    with patch("app.ai.agents.style_learner.chroma_client.get", AsyncMock()) as mock_get:
        await style_learner.learn("Manga CV ingizni yuboring")
    mock_get.assert_not_called()


# ─── promptga beriladigan blok ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_style_context_warns_against_copying_content():
    """Namunalar OHANG uchun ekani promptda aniq yozilgan bo'lishi shart."""
    with patch("app.ai.agents.style_learner.chroma_client.query",
               AsyncMock(return_value={"documents": [["Yaxshi, ko'rib chiqaman"]]})), \
         patch.object(style_learner, "_load_card", AsyncMock(return_value=None)), \
         patch.object(style_learner._embedder, "embed_one", return_value=[0.1]):
        ctx = await style_learner.get_style_context("salom")

    assert "FAQAT OHANG" in ctx
    assert "TAKRORLAMA" in ctx
    assert "CV" in ctx  # aynan shu xato nomma-nom taqiqlangan
    assert "Yaxshi, ko'rib chiqaman" in ctx


@pytest.mark.asyncio
async def test_style_context_includes_card_when_available():
    card = {"card": "Qisqa, iliq va samimiy yoz; emoji kam ishlat.", "sample_count": 12}
    with patch("app.ai.agents.style_learner.chroma_client.query",
               AsyncMock(return_value={"documents": [[]]})), \
         patch.object(style_learner, "_load_card", AsyncMock(return_value=card)), \
         patch.object(style_learner._embedder, "embed_one", return_value=[0.1]):
        ctx = await style_learner.get_style_context("salom")

    assert "Qisqa, iliq va samimiy yoz" in ctx
    assert "FAQAT OHANG" in ctx


@pytest.mark.asyncio
async def test_style_context_empty_when_nothing_learned():
    with patch("app.ai.agents.style_learner.chroma_client.query",
               AsyncMock(return_value={"documents": [[]]})), \
         patch.object(style_learner, "_load_card", AsyncMock(return_value=None)), \
         patch.object(style_learner._embedder, "embed_one", return_value=[0.1]):
        assert await style_learner.get_style_context("salom") == ""


# ─── uslub kartasi ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_card_not_built_before_minimum_samples():
    with patch.object(style_learner, "rebuild_card", AsyncMock()) as rebuild:
        await style_learner._maybe_refresh_card(MIN_SAMPLES_FOR_CARD - 1)
    rebuild.assert_not_called()


@pytest.mark.asyncio
async def test_card_rebuilds_only_after_enough_new_samples():
    existing = {"card": "eski karta", "sample_count": 20}
    with patch.object(style_learner, "_load_card", AsyncMock(return_value=existing)), \
         patch.object(style_learner, "rebuild_card", AsyncMock()) as rebuild:
        await style_learner._maybe_refresh_card(20 + CARD_REFRESH_EVERY - 1)
        rebuild.assert_not_called()

        await style_learner._maybe_refresh_card(20 + CARD_REFRESH_EVERY)
        rebuild.assert_awaited_once()


@pytest.mark.asyncio
async def test_rebuild_card_asks_llm_to_ignore_content():
    captured = {}

    async def fake(messages, **kw):
        captured["prompt"] = messages[0]["content"]
        return "  Iliq, qisqa yoz.  "

    samples = [f"Namuna xabar raqami {i}, yetarlicha uzun matn" for i in range(10)]
    with patch("app.ai.agents.style_learner.chroma_client.get",
               AsyncMock(return_value={"documents": samples})), \
         patch("app.ai.agents.base_agent.BaseAgent._call_llm", AsyncMock(side_effect=fake)), \
         patch.object(style_learner, "_save_card", AsyncMock()) as save:
        card = await style_learner.rebuild_card()

    assert card == "Iliq, qisqa yoz."
    assert "MAZMUNI" in captured["prompt"]        # mazmun emas, manera so'ralgan
    assert "Namuna xabar raqami 0" in captured["prompt"]
    save.assert_awaited_once()


# ─── eski namunalarni tozalash ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_purge_removes_only_unlearnable_samples():
    """Darvoza qo'shilishidan OLDIN saqlangan hujjat so'rovlari o'chiriladi."""
    stored = {
        "ids": ["ok1", "bad1", "ok2", "bad2"],
        "documents": [
            "Assalomu alaykum, ertaga uchrashamizmi?",
            "Manga CV sini tashab bera olasizmi?",
            "Yaxshi, men buni ko'rib chiqaman",
            "Obyektivkangizni yuboring iltimos",
        ],
    }
    with patch("app.ai.agents.style_learner.chroma_client.get", AsyncMock(return_value=stored)), \
         patch("app.ai.agents.style_learner.chroma_client.delete", AsyncMock()) as mock_delete, \
         patch.object(style_learner, "_discard_card", AsyncMock()) as discard, \
         patch.object(style_learner, "rebuild_card", AsyncMock()) as rebuild:
        removed = await style_learner.purge_unlearnable()

    assert removed == 2
    assert mock_delete.call_args.kwargs["ids"] == ["bad1", "bad2"]
    discard.assert_awaited_once()   # eski karta o'sha namunalardan qurilgan edi
    rebuild.assert_awaited_once()


@pytest.mark.asyncio
async def test_purge_does_nothing_when_all_samples_are_clean():
    stored = {
        "ids": ["a", "b"],
        "documents": ["Ertaga soat 10 da bo'lamiz", "Rahmat, juda foydali bo'ldi"],
    }
    with patch("app.ai.agents.style_learner.chroma_client.get", AsyncMock(return_value=stored)), \
         patch("app.ai.agents.style_learner.chroma_client.delete", AsyncMock()) as mock_delete, \
         patch.object(style_learner, "rebuild_card",
                      AsyncMock(side_effect=AssertionError("karta qayta qurilmasligi kerak"))):
        removed = await style_learner.purge_unlearnable()

    assert removed == 0
    mock_delete.assert_not_called()


@pytest.mark.asyncio
async def test_purge_survives_chroma_failure():
    with patch("app.ai.agents.style_learner.chroma_client.get",
               AsyncMock(side_effect=RuntimeError("chroma yo'q"))):
        assert await style_learner.purge_unlearnable() == 0


@pytest.mark.asyncio
async def test_rebuild_card_skipped_when_too_few_samples():
    with patch("app.ai.agents.style_learner.chroma_client.get",
               AsyncMock(return_value={"documents": ["bitta namuna xabar"]})), \
         patch("app.ai.agents.base_agent.BaseAgent._call_llm",
               AsyncMock(side_effect=AssertionError("chaqirilmasligi kerak"))):
        assert await style_learner.rebuild_card() is None
