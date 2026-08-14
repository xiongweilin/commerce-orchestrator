from typing import Protocol

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer


class Embedder(Protocol):
    def encode(self, texts: list[str]) -> np.ndarray: ...


class TfidfEmbedder:
    def __init__(self) -> None:
        self.vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(2, 4), min_df=1)

    def encode(self, texts: list[str]) -> np.ndarray:
        return self.vectorizer.fit_transform(texts).toarray()


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError("install the 'embedding' extra to use BGE embeddings") from exc
        self.model = SentenceTransformer(model_name)

    def encode(self, texts: list[str]) -> np.ndarray:
        return np.asarray(
            self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        )


def blended_embeddings(
    embedder: Embedder,
    raw_texts: list[str],
    summaries: list[str],
    raw_weight: float,
) -> np.ndarray:
    if len(raw_texts) != len(summaries):
        raise ValueError("raw texts and summaries must have equal length")
    if not 0 <= raw_weight <= 1:
        raise ValueError("raw weight must be between 0 and 1")
    raw_vectors = _normalize(np.asarray(embedder.encode(raw_texts), dtype=float))
    if raw_weight == 1:
        return raw_vectors
    summary_vectors = _normalize(np.asarray(embedder.encode(summaries), dtype=float))
    if raw_weight == 0:  # NOSONAR - false positive: weight is [0,1) here, 0 legit
        return summary_vectors
    return np.concatenate(
        (
            np.sqrt(raw_weight) * raw_vectors,
            np.sqrt(1 - raw_weight) * summary_vectors,
        ),
        axis=1,
    )


def _normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1
    return vectors / norms
