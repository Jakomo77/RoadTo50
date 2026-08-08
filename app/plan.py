import json
import datetime
from pathlib import Path

BDAY = datetime.date(2027, 8, 3)
_PLAN_PATH = Path(__file__).resolve().parent.parent / "data" / "redaktionsplan.json"

_plan_cache: list[dict] | None = None


def _load_plan() -> list[dict]:
    global _plan_cache
    if _plan_cache is None:
        with open(_PLAN_PATH, encoding="utf-8") as f:
            _plan_cache = json.load(f)
    return _plan_cache


def days_left(today: datetime.date | None = None) -> int:
    today = today or datetime.date.today()
    return max(0, (BDAY - today).days)


def todays_entries(today: datetime.date | None = None) -> list[dict]:
    """Alle geplanten Posts fuer heute laut dem 52-Wochen-Redaktionsplan
    (data/redaktionsplan.json, identisch mit dem Dashboard-Artifact und der xlsx)."""
    today = today or datetime.date.today()
    iso = today.isoformat()
    return [row for row in _load_plan() if row["Datum"] == iso]


def todays_summary(today: datetime.date | None = None) -> str | None:
    entries = todays_entries(today)
    if not entries:
        return None
    parts = [
        f"[{e['Matrix-Kategorie']}/{e['Skill-Fokus']}] {e['Content-Idee/Hook']}"
        for e in entries
    ]
    return " | ".join(parts)
