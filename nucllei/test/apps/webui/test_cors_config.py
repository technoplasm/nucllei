"""CORS_ALLOW_ORIGIN の空指定(llens 2026-10-03): 空文字は「同一 origin のみ」— 許可 origin の
list は空、`*` の WARNING も出ない。機ごとの origin を env に持たないための約束。"""

import logging

import pytest

from nucllei import config


def test_empty_cors_means_same_origin_only(caplog):
    with caplog.at_level(logging.WARNING, logger=config.log.name):
        assert config.parse_cors_allow_origin("") == []
        assert config.parse_cors_allow_origin(" ; ") == []
    assert "CORS_ALLOW_ORIGIN IS SET TO '*'" not in caplog.text


def test_cors_list_drops_blank_elements():
    assert config.parse_cors_allow_origin("http://a.example;; https://b.example ;") == [
        "http://a.example",
        "https://b.example",
    ]


def test_cors_star_warns(caplog):
    with caplog.at_level(logging.WARNING, logger=config.log.name):
        assert config.parse_cors_allow_origin("*") == ["*"]
    assert "CORS_ALLOW_ORIGIN IS SET TO '*'" in caplog.text


def test_cors_invalid_origin_rejected():
    with pytest.raises(ValueError):
        config.parse_cors_allow_origin("ftp://x.example")
