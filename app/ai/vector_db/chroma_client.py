import chromadb
from chromadb.config import Settings as ChromaSettings
from loguru import logger
from app.config import settings


class ChromaDBClient:
    _instance: "ChromaDBClient | None" = None

    def __init__(self) -> None:
        self._client = None
        self._collection = None

    @classmethod
    def get_instance(cls) -> "ChromaDBClient":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def _ensure_client(self) -> None:
        if self._client is None:
            self._client = await chromadb.AsyncHttpClient(
                host=settings.chroma_host,
                port=settings.chroma_port,
                settings=ChromaSettings(anonymized_telemetry=False),
            )

    async def get_collection(self):
        await self._ensure_client()
        if self._collection is None:
            self._collection = await self._client.get_or_create_collection(
                name=settings.chroma_collection,
                metadata={"hnsw:space": "cosine"},
            )
        return self._collection

    async def upsert(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict],
    ) -> None:
        collection = await self.get_collection()
        await collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )
        logger.debug(f"ChromaDB: {len(ids)} ta yozuv saqlandi")

    async def query(
        self,
        query_embeddings: list[list[float]],
        n_results: int = 5,
        where: dict | None = None,
    ) -> dict:
        collection = await self.get_collection()
        kwargs: dict = {
            "query_embeddings": query_embeddings,
            "n_results": n_results,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where
        return await collection.query(**kwargs)

    async def delete(self, ids: list[str]) -> None:
        collection = await self.get_collection()
        await collection.delete(ids=ids)

    async def delete_by_user(self, user_id: int) -> None:
        collection = await self.get_collection()
        await collection.delete(where={"user_id": user_id})

    async def count(self) -> int:
        collection = await self.get_collection()
        return await collection.count()


chroma_client = ChromaDBClient.get_instance()
