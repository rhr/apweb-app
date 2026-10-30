"""Paths and settings, overridable by environment variables."""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def data_dir() -> Path:
    return Path(os.environ.get("APW_DATA", REPO_ROOT / "data"))


def db_path() -> Path:
    return Path(os.environ.get("APW_DB", data_dir() / "apw.sqlite"))


def user_agent() -> str:
    ua = "apw-knowledge-graph/0.1 (angiosperm phylogeny research tool; set APW_CONTACT)"
    contact = os.environ.get("APW_CONTACT")
    return f"{ua}; contact: {contact}" if contact else ua
