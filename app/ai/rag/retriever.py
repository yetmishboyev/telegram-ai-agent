from loguru import logger
from app.ai.rag.embedder import get_embedder
from app.ai.vector_db.chroma_client import chroma_client


class RAGRetriever:
    """Semantic search orqali tegishli xotira va ma'lumotlarni topadi."""

    def __init__(self) -> None:
        self._embedder = get_embedder()

    async def retrieve(
        self,
        query: str,
        user_id: int,
        n_results: int = 5,
        doc_type: str | None = None,
    ) -> list[dict]:
        """Semantic search qiladi va tegishli hujjatlarni qaytaradi."""
        query_embedding = self._embedder.embed_one(query)

        where: dict = {"user_id": user_id}
        if doc_type:
            where["type"] = doc_type

        try:
            results = await chroma_client.query(
                query_embeddings=[query_embedding],
                n_results=n_results,
                where=where,
            )
        except Exception as e:
            logger.warning(f"RAG qidiruvda xato: {e}")
            return []

        if not results["documents"] or not results["documents"][0]:
            return []

        items = []
        docs = results["documents"][0]
        metas = results["metadatas"][0]
        dists = results["distances"][0]

        for doc, meta, dist in zip(docs, metas, dists):
            similarity = 1 - dist  # cosine distance → similarity
            if similarity >= 0.4:
                items.append({
                    "content": doc,
                    "metadata": meta,
                    "similarity": round(similarity, 4),
                })

        items.sort(key=lambda x: x["similarity"], reverse=True)
        logger.debug(f"RAG: {len(items)} ta natija topildi (query: {query[:50]})")
        return items

    async def retrieve_memories(self, query: str, user_id: int) -> list[dict]:
        return await self.retrieve(query, user_id, n_results=5, doc_type="memory")

    async def retrieve_summaries(self, query: str, user_id: int) -> list[dict]:
        return await self.retrieve(query, user_id, n_results=3, doc_type="summary")

    def format_context(self, items: list[dict]) -> str:
        """Natijalarni prompt uchun matn formatiga o'tkazadi."""
        if not items:
            return ""
        lines = ["## Tegishli xotira:"]
        for item in items:
            lines.append(f"- {item['content']} (o'xshashlik: {item['similarity']:.0%})")
        return "\n".join(lines)


rag_retriever = RAGRetriever()
