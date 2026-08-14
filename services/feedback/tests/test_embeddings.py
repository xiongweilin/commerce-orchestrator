import numpy as np
import pytest

from feedback_app.embeddings import TfidfEmbedder, blended_embeddings


class FakeEmbedder:
    def encode(self, texts: list[str]) -> np.ndarray:
        mapping = {
            "raw-a": [1.0, 0.0],
            "raw-b": [0.0, 1.0],
            "summary-a": [1.0, 0.0],
            "summary-b": [1.0, 0.0],
        }
        return np.asarray([mapping[text] for text in texts], dtype=float)


def test_blended_embeddings_preserve_weighted_cosine_contract() -> None:
    vectors = blended_embeddings(
        FakeEmbedder(),
        ["raw-a", "raw-b"],
        ["summary-a", "summary-b"],
        raw_weight=0.8,
    )
    assert np.isclose(vectors[0] @ vectors[1], 0.2)
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1)


def test_blended_embeddings_reject_invalid_contract() -> None:
    with pytest.raises(ValueError, match="equal length"):
        blended_embeddings(FakeEmbedder(), ["raw-a"], [], 0.8)
    with pytest.raises(ValueError, match="between"):
        blended_embeddings(FakeEmbedder(), ["raw-a"], ["summary-a"], 1.1)


def test_tfidf_embedder_returns_one_vector_per_text() -> None:
    vectors = TfidfEmbedder().encode(["alpha", "beta"])

    assert vectors.shape[0] == 2
    assert vectors.shape[1] > 0


def test_blended_embeddings_can_return_raw_or_summary_only() -> None:
    raw_only = blended_embeddings(FakeEmbedder(), ["raw-a"], ["summary-a"], raw_weight=1)
    summary_only = blended_embeddings(FakeEmbedder(), ["raw-b"], ["summary-b"], raw_weight=0)

    assert np.allclose(raw_only, [[1.0, 0.0]])
    assert np.allclose(summary_only, [[1.0, 0.0]])


def test_blended_embeddings_normalizes_zero_vectors() -> None:
    class ZeroEmbedder:
        def encode(self, texts: list[str]) -> np.ndarray:
            return np.zeros((len(texts), 2), dtype=float)

    vectors = blended_embeddings(ZeroEmbedder(), ["raw"], ["summary"], raw_weight=0.5)

    assert np.allclose(vectors, [[0.0, 0.0, 0.0, 0.0]])
