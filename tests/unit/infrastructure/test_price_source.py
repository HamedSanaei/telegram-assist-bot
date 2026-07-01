"""Unit tests for the HTTP JSON price source path extraction."""

from __future__ import annotations

import pytest

from src.infrastructure.price.http_price_source import extract_json_path
from src.shared.errors import PriceFetchError


class TestExtractJsonPath:
    """Tests for :func:`extract_json_path`."""

    def test_nested_dict_path(self) -> None:
        data = {"data": {"usd": {"price": "61,500"}}}
        assert extract_json_path(data, "data.usd.price") == "61,500"

    def test_list_index_path(self) -> None:
        data = {"rates": [{"value": 61500}]}
        assert extract_json_path(data, "rates.0.value") == 61500

    def test_missing_key_raises(self) -> None:
        with pytest.raises(PriceFetchError):
            extract_json_path({"a": 1}, "b")

    def test_empty_path_returns_root(self) -> None:
        assert extract_json_path(61500, "") == 61500
