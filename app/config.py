"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# ── Defaults ──────────────────────────────────────────────────────────────
DEFAULT_COUNTRY: str = "India"
DEFAULT_CURRENCY: str = "INR"


@dataclass(frozen=True)
class Settings:
    """Immutable application settings."""

    db_path: Path


def get_settings() -> Settings:
    """Build settings from environment, falling back to sensible defaults."""
    raw_path = os.getenv("APP_DB_PATH", "data/app.db")
    return Settings(db_path=Path(raw_path))
