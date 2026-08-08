"""
Instagram-Veroeffentlichung ueber die Meta Graph API (Business Login for Instagram).

STATUS: Geruest/Stub. Funktioniert erst, sobald IG_ACCESS_TOKEN und
IG_BUSINESS_ACCOUNT_ID gesetzt sind (siehe README, Abschnitt "Schritt 2: Instagram
anbinden"). Bis dahin werfen die Funktionen bewusst einen klaren Fehler statt still
zu scheitern.

Ablauf laut Meta-Doku (zwei Schritte):
  1. POST /{ig-user-id}/media       -> erstellt einen Media-Container (Video-URL, Caption)
  2. POST /{ig-user-id}/media_publish -> veroeffentlicht den Container als Reel

Wichtig: Instagram braucht eine oeffentlich erreichbare Video-URL (kein Datei-Upload-Body).
Das Video muss also zwischen Telegram-Download und Instagram-Publish irgendwo kurz
oeffentlich gehostet werden (z.B. Railway-Volume + eigene Static-Route, oder ein
Objektspeicher wie Cloudflare R2 / S3). Das ist der naechste Baustein, siehe README.
"""

import requests
from .config import settings

GRAPH_API_BASE = "https://graph.instagram.com/v21.0"


class InstagramNotConfigured(Exception):
    pass


def _require_config():
    if not settings.ig_access_token or not settings.ig_business_account_id:
        raise InstagramNotConfigured(
            "Instagram ist noch nicht angebunden. IG_ACCESS_TOKEN und "
            "IG_BUSINESS_ACCOUNT_ID in den Railway-Env-Vars setzen (siehe README)."
        )


def create_media_container(video_public_url: str, caption: str) -> str:
    """Schritt 1: Media-Container fuer ein Reel anlegen. Gibt die container_id zurueck."""
    _require_config()
    url = f"{GRAPH_API_BASE}/{settings.ig_business_account_id}/media"
    resp = requests.post(
        url,
        data={
            "media_type": "REELS",
            "video_url": video_public_url,
            "caption": caption,
            "access_token": settings.ig_access_token,
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def publish_media(container_id: str) -> str:
    """Schritt 2: veroeffentlicht den zuvor erstellten Container. Gibt die media_id zurueck."""
    _require_config()
    url = f"{GRAPH_API_BASE}/{settings.ig_business_account_id}/media_publish"
    resp = requests.post(
        url,
        data={"creation_id": container_id, "access_token": settings.ig_access_token},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def publish_reel(video_public_url: str, caption: str) -> str:
    """Komfort-Funktion: Container erstellen + sofort veroeffentlichen."""
    container_id = create_media_container(video_public_url, caption)
    return publish_media(container_id)
