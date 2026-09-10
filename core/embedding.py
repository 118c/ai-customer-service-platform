"""Deterministic local embeddings used for offline-safe Chroma collections."""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
from typing import Any, Dict, List

from chromadb.api.types import Documents, EmbeddingFunction, Embeddings


class HashEmbeddingFunction(EmbeddingFunction[Documents]):
    """Compact character n-gram embedding with no model download requirement."""

    dimension = 384

    def __init__(self) -> None:
        pass

    @staticmethod
    def name() -> str:
        return "employee-service-hash-embedding-v1"

    @staticmethod
    def build_from_config(config: Dict[str, Any]) -> "HashEmbeddingFunction":
        return HashEmbeddingFunction()

    def get_config(self) -> Dict[str, Any]:
        return {"dimension": self.dimension, "algorithm": "blake2b-char-ngram-v1"}

    def __call__(self, input: Documents) -> Embeddings:
        return [self._embed(text) for text in input]

    def embed_query(self, input: Documents) -> Embeddings:
        return self(input)

    def _embed(self, text: str) -> List[float]:
        normalized = " ".join(str(text or "").lower().split())
        grams = list(normalized) + [normalized[index:index + 2] for index in range(max(0, len(normalized) - 1))]
        vector = [0.0] * self.dimension
        for gram in grams:
            digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimension
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[bucket] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


def local_data_path(configured_path: str) -> str:
    """Translate container-style /app paths when the service runs on Windows."""
    value = str(configured_path or "./data/chroma")
    if os.name == "nt" and value.replace("\\", "/").startswith("/app/"):
        project_root = Path(__file__).resolve().parents[1]
        value = str(project_root / value.replace("\\", "/")[5:])
    path = Path(value).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return str(path)
