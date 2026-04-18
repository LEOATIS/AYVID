"""Tests for NER pipeline JSON parsing and helper functions."""
from __future__ import annotations

import pytest


class TestStripJsonFences:
    def test_strips_json_code_block(self):
        from data_primitives.extractors.ner_pipeline import _strip_json_fences
        raw = '```json\n{"key": "value"}\n```'
        assert _strip_json_fences(raw) == '{"key": "value"}'

    def test_strips_plain_code_block(self):
        from data_primitives.extractors.ner_pipeline import _strip_json_fences
        raw = '```\n{"key": "value"}\n```'
        assert _strip_json_fences(raw) == '{"key": "value"}'

    def test_passthrough_clean_json(self):
        from data_primitives.extractors.ner_pipeline import _strip_json_fences
        raw = '{"key": "value"}'
        assert _strip_json_fences(raw) == '{"key": "value"}'


class TestParseLlmJson:
    def test_valid_json(self):
        from data_primitives.extractors.ner_pipeline import _parse_llm_json
        result = _parse_llm_json('{"entities": []}')
        assert result == {"entities": []}

    def test_json_with_fences(self):
        from data_primitives.extractors.ner_pipeline import _parse_llm_json
        result = _parse_llm_json('```json\n{"entities": ["AAPL"]}\n```')
        assert result == {"entities": ["AAPL"]}

    def test_invalid_json_returns_none(self):
        from data_primitives.extractors.ner_pipeline import _parse_llm_json
        result = _parse_llm_json("not valid json at all")
        assert result is None


class TestSequenceCounter:
    def test_monotonic_ids(self):
        from data_primitives.extractors.ner_pipeline import _next_seq
        a = _next_seq()
        b = _next_seq()
        c = _next_seq()
        assert b > a
        assert c > b
