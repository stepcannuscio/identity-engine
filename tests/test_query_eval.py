"""Regression tests for the deterministic query usefulness eval corpus."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.query_eval import evaluate_corpus


def test_query_eval_corpus_v1_passes():
    results = evaluate_corpus("v1")
    assert results
    assert all(result.passed for result in results)
