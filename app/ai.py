import datetime
import json
from typing import Literal

from anthropic import Anthropic
from pydantic import BaseModel, Field, ValidationError

from .config import settings
from .profile import profile_as_prompt_block
from . import greger

_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=settings.anthropic_api_key)
    return _client


class CaptionGenerationError(Exception):
    """Wird geworfen, wenn Claude auch nach einem Korrektur-Versuch kein
    gueltiges, strukturiertes Posting liefert. Der Aufrufer (telegram_bot.py)
    faengt das ab und meldet Jakob einen klaren Fehler statt abzustuerzen."""


class Posting(BaseModel):
    """Strikt validierte Form eines fertigen Postings. Ersetzt das vorherige
    naive json.loads() -- Claude liefert das Ergebnis ueber Tool-Use direkt als
    strukturiertes Objekt, das hier zusaetzlich per Pydantic geprueft wird."""

    hook: str = Field(min_length=1, description="Ein Satz, der in den ersten 3 Sekunden funktioniert")
    caption: str = Field(min_length=1, description="3-5 Saetze Caption im beschriebenen Ton")
    hashtags: list[str] = Field(default_factory=list)
    matrix_kategorie: Literal["Education", "Inspiration", "Entertainment", "Promotion"]


POSTING_TOOL = {
    "name": "emit_posting",
    "description": "Gibt das fertige Instagram/YouTube-Posting strukturiert zurueck.",
    "input_schema": {
        "type": "object",
        "properties": {
            "hook": {"type": "string", "description": "Ein Satz, der in den ersten 3 Sekunden funktioniert"},
            "caption": {"type": "string", "description": "3-5 Saetze Caption im beschriebenen Ton"},
            "hashtags": {"type": "array", "items": {"type": "string"}},
            "matrix_kategorie": {
                "type": "string",
                "enum": ["Education", "Inspiration", "Entertainment", "Promotion"],
            },
        },
        "required": ["hook", "caption", "hashtags", "matrix_kategorie"],
    },
}

CAPTION_SYSTEM_PROMPT = """Du bist der KI-Content-Assistent fuer folgenden Instagram/YouTube-Account:

{profile}

Deine Aufgabe: Aus einer kurzen Nutzerbeschreibung eines rohen Video-Clips erstellst du
sofort ein fertiges Posting. Rufe dafuer IMMER das Tool 'emit_posting' mit den
fertigen Werten auf -- antworte nicht als Freitext und nicht als JSON im Fliesstext.
"""


def _extract_tool_input(resp, tool_name: str) -> dict:
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
            return block.input
    raise CaptionGenerationError(
        f"Claude hat das Tool '{tool_name}' nicht aufgerufen -- keine strukturierte Antwort erhalten."
    )


def _call_for_posting(system: str, messages: list[dict], max_tokens: int = 800) -> dict:
    """Ruft Claude per Tool-Use (erzwungene strukturierte Ausgabe) auf und
    validiert das Ergebnis mit Pydantic. Schlaegt die Validierung fehl, wird
    Claude einmalig mit dem konkreten Fehler zur Korrektur aufgefordert, bevor
    endgueltig ein CaptionGenerationError geworfen wird."""
    client = _get_client()

    def _call(msgs: list[dict]):
        return client.messages.create(
            model=settings.anthropic_model,
            max_tokens=max_tokens,
            system=system,
            tools=[POSTING_TOOL],
            tool_choice={"type": "tool", "name": POSTING_TOOL["name"]},
            messages=msgs,
        )

    resp = _call(messages)
    raw = _extract_tool_input(resp, POSTING_TOOL["name"])
    try:
        return Posting.model_validate(raw).model_dump()
    except ValidationError as first_error:
        # Einmaliger Korrektur-Versuch: Fehler + bisherigen Verlauf zurueckspielen.
        retry_messages = messages + [
            {"role": "assistant", "content": resp.content},
            {
                "role": "user",
                "content": (
                    "Deine letzte Antwort war ungueltig:\n"
                    f"{first_error}\n\n"
                    "Rufe 'emit_posting' erneut auf und behebe genau diese Fehler "
                    "(z.B. fehlende Felder ergaenzen, matrix_kategorie exakt einer der "
                    "erlaubten Optionen zuordnen)."
                ),
            },
        ]
        try:
            retry_resp = _call(retry_messages)
            raw2 = _extract_tool_input(retry_resp, POSTING_TOOL["name"])
            return Posting.model_validate(raw2).model_dump()
        except (ValidationError, CaptionGenerationError) as second_error:
            raise CaptionGenerationError(
                "Claude hat auch nach einem Korrektur-Versuch kein gueltiges Posting "
                f"geliefert: {second_error}"
            ) from second_error


MULTI_CLIP_PROMPT_BLOCK = """WICHTIG -- Mehrteiliger Clip: Dieses Video ist kein einzelner Take, sondern besteht
aus {clip_count} per FFmpeg chronologisch zusammengeschnittenen Roh-Clips (ein Reel).
Die Nutzerbeschreibung benennt die Clips vermutlich einzeln (z.B. 'Clip 1: ..., Clip 2:
...'). Gestalte Hook und Caption dramaturgisch passend zur Abfolge -- z.B. Vorher/
Nachher, Fail-dann-Erfolg, sichtbarer Fortschritt ueber die Clips hinweg -- statt die
Clips isoliert zu behandeln."""


def generate_caption(
    video_description: str,
    todays_plan_idea: str | None = None,
    plan_kategorie: str | None = None,
    skill_focus: str | None = None,
    clip_count: int = 1,
) -> dict:
    """Erstellt aus einer kurzen Beschreibung des Roh-Clips ein fertiges Posting
    (Hook, Caption, Hashtags, Matrix-Kategorie).

    plan_kategorie/skill_focus kommen aus dem heutigen Redaktionsplan-Eintrag
    (siehe plan.todays_entries()). Steht dort 'Education', wird passendes
    Hintergrundwissen aus der Greger-Wissensbasis in den System-Prompt gemischt.

    clip_count > 1 bedeutet, dass storage.stitch_videos() mehrere Roh-Clips zu
    diesem einen Video zusammengefuegt hat (siehe telegram_bot.py) -- die KI
    bekommt das mitgeteilt, um Hook/Caption dramaturgisch auf die Clip-Abfolge
    statt auf einen einzelnen Take auszurichten.

    Wirft CaptionGenerationError, wenn Claude auch nach einem Korrektur-Versuch
    kein gueltiges Posting liefert -- das faengt der Telegram-Handler ab."""
    system = CAPTION_SYSTEM_PROMPT.format(profile=profile_as_prompt_block())
    if clip_count and clip_count > 1:
        system += "\n\n" + MULTI_CLIP_PROMPT_BLOCK.format(clip_count=clip_count)
    if plan_kategorie and plan_kategorie.strip().lower() == "education":
        greger_block = greger.facts_prompt_block(skill_focus=skill_focus)
        if greger_block:
            system += "\n\n" + greger_block

    user_msg = f"Beschreibung des Roh-Clips: {video_description}"
    if todays_plan_idea:
        user_msg += f"\n\nGeplante Content-Idee fuer heute laut Redaktionsplan: {todays_plan_idea}"

    return _call_for_posting(system, [{"role": "user", "content": user_msg}])


def revise_caption(previous: dict, feedback: str) -> dict:
    """Ueberarbeitet ein bereits generiertes Posting anhand von Jakobs Feedback
    (z.B. per Sprachbefehl/Text: 'Mach den Hook am Anfang frecher').

    Wirft CaptionGenerationError, wenn Claude auch nach einem Korrektur-Versuch
    kein gueltiges Posting liefert."""
    system = CAPTION_SYSTEM_PROMPT.format(profile=profile_as_prompt_block())
    if str(previous.get("matrix_kategorie", "")).strip().lower() == "education":
        greger_block = greger.facts_prompt_block(skill_focus=None)
        if greger_block:
            system += "\n\n" + greger_block

    user_msg = (
        f"Bisheriges Posting: {json.dumps(previous, ensure_ascii=False)}\n\n"
        f"Feedback von Jakob: {feedback}\n\n"
        "Rufe 'emit_posting' mit dem ueberarbeiteten Posting auf."
    )
    return _call_for_posting(system, [{"role": "user", "content": user_msg}])


def morning_briefing(todays_plan_idea: str | None, days_left: int) -> str:
    """Erzeugt den kurzen Push-Text fuers Morgen-Briefing. Nutzt aktuell keine
    Live-Trendsuche -- das ist der naechste Ausbauschritt (siehe README, Abschnitt
    'Naechste Schritte': Web-Suche/Trend-Scan in diesen Aufruf einhaengen)."""
    client = _get_client()
    system = (
        "Du bist ein fordernder, aber unterstuetzender Content-Coach fuer folgenden Account:\n\n"
        + profile_as_prompt_block()
    )
    user_msg = (
        f"Schreibe eine kurze, knackige Morgen-Push-Nachricht (2-4 Saetze, Deutsch, direkt, "
        f"kein Smalltalk). Noch {days_left} Tage bis zum Ziel-Datum. "
        f"Heutige geplante Content-Idee laut Redaktionsplan: {todays_plan_idea or 'keine hinterlegt'}. "
        "Motiviere knapp und fordernd zum Drehen des heutigen Clips."
    )
    resp = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=300,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )
    return resp.content[0].text.strip()
