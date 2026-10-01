"""Load config/settings.yaml and resolve project-relative paths."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "settings.yaml"


def resolve(path: str | Path | None) -> Path | None:
    """Absolute path for a config value; relative paths hang off the project root."""
    if not path:
        return None
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    with open(path or DEFAULT_CONFIG, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_glossary(path: str | Path | None) -> dict[str, str]:
    p = resolve(path)
    if not p or not p.exists():
        return {}
    with open(p, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return {str(k): str(v) for k, v in data.items()}
