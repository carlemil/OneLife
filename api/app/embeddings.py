"""Pluggable text embeddings (MEMORY_AND_LEAKAGE.md §2).

Anthropic has no embeddings endpoint; real semantic embeddings would come from a
dedicated provider (e.g. Voyage AI). For the slice we use a deterministic,
zero-dependency hashed bag-of-words embedding so the pipeline runs with no extra
config. Cosine similarity then reflects lexical overlap — crude but functional.

To go production: replace embed() with a provider call and update EMBED_DIM
(and the vector(N) column in db/03_memory.sql) to the provider's dimension.
"""
import hashlib
import math
import re

EMBED_DIM = 256


def embed(text: str) -> list[float]:
    vec = [0.0] * EMBED_DIM
    for tok in re.findall(r"[a-z0-9]+", text.lower()):
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        idx = h % EMBED_DIM
        sign = 1.0 if (h >> 8) & 1 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def to_pgvector(vec: list[float]) -> str:
    """pgvector literal — passed as text and cast ::vector in SQL."""
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"
