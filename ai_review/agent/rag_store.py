import hashlib
import json
import os
import chromadb
from chromadb.utils import embedding_functions

basedir = os.path.dirname(os.path.abspath(__file__))


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


class RAGStore:
    """RAG vector store for content moderation examples with MD5 dedup."""

    def __init__(self, persist_dir=None):
        persist_dir = persist_dir or os.path.join(basedir, "chroma_db")
        self.client = chromadb.PersistentClient(path=persist_dir)
        self.ef = embedding_functions.DefaultEmbeddingFunction()

        existing = [c.name for c in self.client.list_collections()]
        if "moderation_examples" in existing:
            self.collection = self.client.get_collection(
                name="moderation_examples",
                embedding_function=self.ef,
            )
        else:
            self.collection = self.client.create_collection(
                name="moderation_examples",
                embedding_function=self.ef,
            )
        # 无论首次还是后续启动，都增量同步 knowledge_base.json
        self._sync_knowledge_base()

    def _md5_exists(self, md5_hash: str) -> bool:
        result = self.collection.get(where={"md5": md5_hash})
        return len(result["ids"]) > 0

    def _sync_knowledge_base(self):
        """增量同步：只加入 knowledge_base.json 中尚未入库的条目。"""
        kb_path = os.path.join(basedir, "knowledge_base.json")
        if not os.path.exists(kb_path):
            return

        with open(kb_path, "r", encoding="utf-8") as f:
            entries = json.load(f)

        new_entries = []
        for e in entries:
            h = _md5(e["text"])
            if not self._md5_exists(h):
                new_entries.append(e)

        if new_entries:
            self.collection.add(
                ids=[e["id"] for e in new_entries],
                documents=[e["text"] for e in new_entries],
                metadatas=[
                    {
                        "type": e["type"],
                        "category": e["category"],
                        "md5": _md5(e["text"]),
                    }
                    for e in new_entries
                ],
            )

    def query(self, text: str, n_results: int = 3) -> list[dict]:
        results = self.collection.query(
            query_texts=[text],
            n_results=n_results,
        )
        output = []
        ids = results["ids"][0]
        docs = results["documents"][0]
        metas = results["metadatas"][0]
        distances = results["distances"][0]

        for i, doc, meta, dist in zip(ids, docs, metas, distances):
            output.append({
                "id": i,
                "text": doc,
                "type": meta.get("type", ""),
                "category": meta.get("category", ""),
                "distance": dist,
            })
        return output

    def add_example(self, entry_id: str, text: str, entry_type: str, category: str):
        """运行时动态添加，MD5 去重。"""
        h = _md5(text)
        if self._md5_exists(h):
            return False

        self.collection.add(
            ids=[entry_id],
            documents=[text],
            metadatas=[{"type": entry_type, "category": category, "md5": h}],
        )
        return True

    def list_all(self) -> list[dict]:
        """返回 RAG 库中所有条目。"""
        if self.collection.count() == 0:
            return []

        result = self.collection.get()
        output = []
        for i, doc, meta in zip(result["ids"], result["documents"], result["metadatas"]):
            output.append({
                "id": i,
                "text": doc,
                "type": meta.get("type", ""),
                "category": meta.get("category", ""),
            })
        return output
