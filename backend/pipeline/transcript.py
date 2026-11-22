from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_transcript(path: Path) -> dict[str, Any] | None:
    """Load a persisted API transcript artifact, if one exists."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
            return None
        return data
    except (OSError, json.JSONDecodeError):
        return None
