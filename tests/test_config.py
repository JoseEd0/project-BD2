"""La configuración rechaza valores fuera de rango al crearse."""

import pytest

from config import MAX_PAGE_SIZE, MIN_PAGE_SIZE, ConfigurationError, EngineConfig

INVALID = [
    {"page_size": MIN_PAGE_SIZE - 1},
    {"page_size": MAX_PAGE_SIZE + 1},
    {"buffer_pool_pages": 0},
    {"sort_buffer_pages": 0},
    {"merge_fan_in": 1},
    {"hash_partitions": 1},
    {"sequential_waste_ratio": 0.0},
    {"sequential_waste_ratio": -1.0},
    {"sequential_fill_factor": 5.0},
    {"sequential_max_chain_pages": 0},
    {"hash_merge_fill": 1.5},
    {"rtree_min_fill": 0.7},
    {"rtree_min_fill": 0.4, "rtree_bulk_fill": 0.3},
    {"inline_string_limit": 0},
    {"text_min_token_length": 0},
    {"text_min_token_length": 5, "text_max_token_length": 4},
    {"spimi_block_bytes": 0},
    {"inverted_max_segments": 0},
    {"inverted_pending_fraction": 0.0},
    {"inverted_deleted_fraction": 1.5},
    {"hnsw_m": 0},
    {"hnsw_ef_construction": 0},
    {"hnsw_ef_search": 0},
    {"vector_rebuild_fraction": 0.0},
    {"vector_exact_filter_rows": -1},
    {"vocabulary_size": 0},
    {"kmeans_iterations": 0},
    {"kmeans_samples_per_word": 0},
    {"vocabulary_training_objects": 0},
    {"bm25_k1": -0.1},
    {"bm25_k1": float("inf")},
    {"bm25_k1": float("nan")},
    {"bm25_b": -0.1},
    {"bm25_b": 1.1},
    {"bm25_b": float("nan")},
    {"lock_timeout_seconds": 0.0},
    {"session_idle_seconds": -5.0},
    {"max_result_rows": 0},
    {"csv_delimiter": ";;"},
    {"csv_encoding": "no-existe"},
    {"page_size": float("nan")},
    {"buffer_pool_pages": float("nan")},
    {"lock_timeout_seconds": float("nan")},
    {"lock_timeout_seconds": float("inf")},
    {"session_idle_seconds": float("inf")},
]


@pytest.mark.parametrize("overrides", INVALID, ids=lambda overrides: ",".join(overrides))
def test_out_of_range_values_are_rejected(overrides: dict):
    with pytest.raises(ConfigurationError):
        EngineConfig(**overrides)


def test_the_defaults_are_valid():
    EngineConfig()


def test_the_bounds_themselves_are_valid():
    EngineConfig(page_size=MIN_PAGE_SIZE, rtree_min_fill=0.5, sequential_waste_ratio=1.0)
    EngineConfig(page_size=MAX_PAGE_SIZE, merge_fan_in=2, hash_partitions=2)
    EngineConfig(text_min_token_length=1, text_max_token_length=1)
    EngineConfig(bm25_k1=0.0, bm25_b=0.0)
    EngineConfig(bm25_k1=0.0, bm25_b=1.0)
