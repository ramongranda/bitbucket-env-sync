import os
from pathlib import Path
import pytest

from bb_sync import (
    normalize_url_for_list,
    parse_repo_list,
    ensure_url_in_repo_list,
)


def test_normalize_url_for_list():
    assert normalize_url_for_list(" https://example.com/repo/ ") == "https://example.com/repo"
    assert normalize_url_for_list("https://example.com/repo") == "https://example.com/repo"


def test_parse_repo_list_comma_and_lines():
    text = "https://a.com/r1,https://b.com/r2\nhttps://c.com/r3"
    res = parse_repo_list(text)
    assert "https://a.com/r1" in res
    assert "https://b.com/r2" in res
    assert "https://c.com/r3" in res
    assert len(res) == 3


def test_ensure_url_in_repo_list_adds_and_dedups():
    env = {"REPO_LIST": "https://a.com/r1\nhttps://b.com/r2"}
    added = ensure_url_in_repo_list(env, "https://c.com/r3")
    assert added is True
    assert "https://c.com/r3" in env["REPO_LIST"]

    # Adding existing (different trailing slash) should not add
    added2 = ensure_url_in_repo_list(env, "https://a.com/r1/")
    assert added2 is False
    # ensure only unique entries
    lines = [l for l in env["REPO_LIST"].splitlines() if l.strip()]
    assert len(set(lines)) == len(lines)
