"""
Dr.-Greger-Wissensbasis ('How Not to Age'): liefert wissenschaftlich fundierte
Kernaussagen, die bei Education-Posts als Kontext in den KI-System-Prompt
eingespeist werden -- statt dass die KI Fakten frei erfindet.

Datenquelle: data/greger_facts.json (paraphrasierte Kernaussagen, siehe Hinweis
in der Datei selbst).
"""

import json
from pathlib import Path

_FACTS_PATH = Path(__file__).resolve().parent.parent / "data" / "greger_facts.json"

_facts_cache: dict | None = None


def _load_facts() -> dict:
    global _facts_cache
    if _facts_cache is None:
        with open(_FACTS_PATH, encoding="utf-8") as f:
            _facts_cache = json.load(f)
    return _facts_cache


def select_facts(skill_focus: str | None = None, max_facts: int = 2) -> list[dict]:
    """Waehlt passende Fakten aus: zuerst nach Skill-Fokus (z.B. 'Handstand' ->
    Gelenke & Sehnen), sonst allgemeine/breit anwendbare Fakten als Fallback."""
    data = _load_facts()
    facts = data.get("facts", [])
    if not facts:
        return []

    if skill_focus:
        matched = [
            f for f in facts
            if skill_focus.lower() in [t.lower() for t in f.get("tags", [])]
        ]
        if matched:
            return matched[:max_facts]

    general = [f for f in facts if "Allgemein" in f.get("tags", [])]
    pool = general or facts
    return pool[:max_facts]


def facts_prompt_block(skill_focus: str | None = None, max_facts: int = 2) -> str:
    """Baut einen Prompt-Block mit ausgewaehlten Greger-Fakten. Leerer String,
    wenn keine Fakten verfuegbar sind (Prompt bleibt dann unveraendert)."""
    facts = select_facts(skill_focus=skill_focus, max_facts=max_facts)
    if not facts:
        return ""

    data = _load_facts()
    quelle = data.get("quelle", "How Not to Age (Michael Greger)")

    lines = [
        f"WISSENSCHAFTLICHES FUNDAMENT (Quelle: {quelle}, paraphrasiert -- "
        "baue passende Punkte natuerlich in den Education-Post ein, wo sie zum "
        "Clip passen. Keine Pflicht, alle zu nutzen. Keine zusaetzlichen Zahlen "
        "oder Studien erfinden, die hier nicht stehen):"
    ]
    for fact in facts:
        lines.append(f"- {fact['fact']}")
    return "\n".join(lines)
