"""style_learner.learn() — dedup va saqlash-chegarasi testlari (roadmap Faza 3, band 7)."""
import pytest
from unittest.mock import AsyncMock, patch

from app.ai.agents.style_learner import style_learner, MAX_STORED_STYLE_EXAMPLES


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
       patch.object(style_learner._embedder, "embed_one", return_value=[0.1, 0.2]):
        await style_learner.learn("Yana bir yangi matn")

    mock_delete.assert_called_once()
    deleted_ids = mock_delete.call_args.kwargs.get("ids", mock_delete.call_args.args[0] if mock_delete.call_args.args else None)
    assert deleted_ids == ["id0"]  # eng eski (saved_at eng kichik) yozuv o'chiriladi
