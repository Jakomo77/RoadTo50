"""
YouTube-Shorts-Veroeffentlichung ueber die YouTube Data API v3.

STATUS: Geruest/Stub. Funktioniert erst, sobald yt_client_secret.json (OAuth-Client
von Google Cloud Console) und ein einmalig erzeugter yt_token.json vorhanden sind
(siehe README, Abschnitt "Schritt 2: YouTube anbinden").

Seit den Quota-Aenderungen 2025/2026 kostet ein Video-Upload nur noch ca. 100 Units
(vorher ~1600) in einem eigenen taeglichen Kontingent -- bis zu 100 Uploads/Tag im
kostenlosen Tier sind damit realistisch nutzbar.
"""

import os
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials

from .config import settings


class YouTubeNotConfigured(Exception):
    pass


def _get_client():
    if not os.path.exists(settings.yt_token_file):
        raise YouTubeNotConfigured(
            "YouTube ist noch nicht angebunden. yt_token.json fehlt "
            "(einmaliger OAuth-Login noetig, siehe README)."
        )
    creds = Credentials.from_authorized_user_file(settings.yt_token_file)
    return build("youtube", "v3", credentials=creds)


def upload_short(local_video_path: str, title: str, description: str, tags: list[str]) -> str:
    """Laedt ein Video als YouTube Short hoch (vertikales 9:16-Format + #Shorts im
    Titel/Beschreibung signalisiert YouTube, dass es sich um ein Short handelt).
    Gibt die YouTube-Video-ID zurueck."""
    youtube = _get_client()
    body = {
        "snippet": {
            "title": title[:100],
            "description": f"{description}\n\n#Shorts",
            "tags": tags,
            "categoryId": "17",  # Sports
        },
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(local_video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = request.execute()
    return response["id"]
