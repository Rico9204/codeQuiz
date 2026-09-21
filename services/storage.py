from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


BASE_DIR = Path(__file__).resolve().parents[1]
SESSION_DIR = BASE_DIR / "data" / "sessions"


def save_session(payload: dict) -> Path:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    session_id = payload.get("session_id") or uuid4().hex[:12]
    payload["session_id"] = session_id
    payload["saved_at"] = datetime.now(timezone.utc).isoformat()

    path = SESSION_DIR / f"{session_id}.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path
