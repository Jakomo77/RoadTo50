"""
Oeffentlicher Zwischenspeicher fuer Video-Clips.

MVP-Loesung: Videos landen lokal im Railway-Dateisystem unter static/videos/
und werden ueber eine FastAPI-StaticFiles-Route oeffentlich erreichbar gemacht
(siehe app/main.py). Das reicht, um Instagrams Pflichtfeld 'video_url' zu
bedienen, ohne einen zusaetzlichen Cloud-Storage-Account einzurichten.

Wichtiger Hinweis fuer Railway: Das Dateisystem ist NICHT persistent ueber
Redeploys hinweg (neuer Container = leeres Dateisystem). Das ist hier
unkritisch, weil Videos nur kurz zwischengespeichert werden muessen, bis
Instagram sie beim Publish-Call abgerufen hat. Fuer eine dauerhafte Video-
Ablage (z.B. Backup aller geposteten Clips) spaeter auf Cloudflare R2 / S3
umsteigen -- dafuer muesste nur video_path_for()/public_video_url() ersetzt
werden, der Rest des Codes bleibt unveraendert.
"""

import asyncio
import logging
import os
import shutil
from pathlib import Path

from .config import settings

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
VIDEOS_DIR = STATIC_DIR / "videos"
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

# Roh-Clips, die noch auf ihre Geschwister-Clips warten (Mediengruppe/schnell
# nacheinander gesendet), landen hier zwischen -- getrennt von VIDEOS_DIR, damit
# sie nicht mit fertig zusammengefuegten Posts verwechselt werden.
RAW_DIR = VIDEOS_DIR / "raw"

# Auf Railway per nixpacks.toml bereitgestellt (siehe README, Abschnitt
# 'FFmpeg auf Railway') -- Nixpacks' Standard-Python-Image bringt ffmpeg NICHT
# automatisch mit, das muss explizit als Nix-Paket angefordert werden.
FFMPEG_BINARY = os.environ.get("FFMPEG_BINARY", "ffmpeg")
FFPROBE_BINARY = os.environ.get("FFPROBE_BINARY", "ffprobe")


class StitchError(Exception):
    """Wird geworfen, wenn ffmpeg mehrere Clips nicht zu einem Reel zusammenfuegen
    konnte (fehlendes Binary, beschaedigter Clip, non-zero Exit-Code, ...)."""


def video_path_for(post_id: int) -> Path:
    """Lokaler Speicherort fuer den Roh-Clip eines Video-Posts."""
    return VIDEOS_DIR / f"{post_id}.mp4"


def public_video_url(post_id: int) -> str:
    """Oeffentliche HTTPS-URL, unter der Meta/Instagram das Video abrufen kann.
    Setzt voraus, dass WEBHOOK_BASE_URL gesetzt ist (Railway-Domain) und die
    Datei bereits unter video_path_for(post_id) existiert."""
    if not settings.webhook_base_url:
        raise RuntimeError(
            "WEBHOOK_BASE_URL ist nicht gesetzt -- ohne oeffentliche Basis-URL "
            "kann keine Video-URL fuer Instagram erzeugt werden (siehe README)."
        )
    base = settings.webhook_base_url.rstrip("/")
    return f"{base}/static/videos/{post_id}.mp4"


def video_exists(post_id: int) -> bool:
    """Prueft, ob der lokale Clip tatsaechlich noch existiert -- relevant, weil
    Railways Dateisystem Redeploys nicht uebersteht und die DB einen video_path
    referenzieren kann, der laengst weg ist."""
    return video_path_for(post_id).is_file()


def cleanup_video(post_id: int) -> None:
    """Loescht den lokal gespeicherten Clip, z.B. nach erfolgreichem Instagram-
    Publish, damit der (begrenzte) Railway-Speicher nicht vollaeuft. Fehlt die
    Datei bereits, passiert nichts -- kein Fehler."""
    path = video_path_for(post_id)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Konnte Video fuer post_id=%s nicht loeschen: %s", post_id, path, exc_info=True)


def raw_clip_path(batch_id: str, index: int) -> Path:
    """Lokaler Zwischenspeicher-Pfad fuer den index-ten Roh-Clip einer noch nicht
    zusammengefuegten Clip-Serie (batch_id gruppiert die Clips einer Serie)."""
    d = RAW_DIR / batch_id
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{index}.mp4"


def cleanup_raw_batch_dir(batch_id: str) -> None:
    """Entfernt das gesamte Zwischenspeicher-Verzeichnis einer Clip-Serie -- als
    Aufraeum-Schritt nach stitch_videos() (das im Erfolgsfall die einzelnen
    Dateien bereits geloescht hat) und als Fallback, falls das Stitching
    fehlschlug und die Roh-Clips nicht mehr gebraucht werden (Nutzer wird in dem
    Fall gebeten, erneut hochzuladen)."""
    d = RAW_DIR / batch_id
    try:
        shutil.rmtree(d, ignore_errors=True)
    except OSError:
        logger.warning("Konnte Batch-Verzeichnis nicht loeschen: %s", d, exc_info=True)


async def _run(cmd: list[str]) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout, stderr


async def _probe_has_audio(path: Path) -> bool:
    """True, wenn der Clip einen Audio-Stream hat. Manche Gym-Aufnahmen (z.B.
    stumm geschaltet, oder Telegram-Video-Notizen) haben keinen -- ohne diese
    Pruefung wuerde ffmpegs concat-Filter mit 'Stream specifier matches no
    streams' abbrechen."""
    code, out, _ = await _run([
        FFPROBE_BINARY, "-v", "error", "-select_streams", "a",
        "-show_entries", "stream=index", "-of", "csv=p=0", str(path),
    ])
    return code == 0 and out.strip() != b""


async def _probe_duration(path: Path) -> float:
    code, out, _ = await _run([
        FFPROBE_BINARY, "-v", "error", "-show_entries", "format=duration",
        "-of", "csv=p=0", str(path),
    ])
    try:
        return float(out.strip())
    except (ValueError, TypeError):
        return 0.0


async def stitch_videos(input_paths: list[Path], output_path: Path) -> None:
    """Fuegt mehrere MP4-Clips chronologisch (Reihenfolge von input_paths) zu
    einem einzigen 9:16-Reel (1080x1920) zusammen.

    Jeder Clip wird einzeln skaliert und auf 1080x1920 zentriert/letterboxed
    (schwarze Balken bei abweichendem Seitenverhaeltnis), damit Clips
    unterschiedlicher Aufloesung/Ausrichtung/Framerate sauber zusammengeschnitten
    werden koennen. Clips ohne Audiospur bekommen automatisch eine stille
    Ersatzspur passender Laenge, damit das gemeinsame Audio-Mapping nicht bricht.

    Loescht die einzelnen Roh-Clips (input_paths) nach erfolgreichem
    Zusammenfuegen. Wirft StitchError bei jedem Fehlschlag (fehlendes
    ffmpeg/ffprobe, beschaedigter Clip, non-zero Exit-Code) -- in dem Fall
    bleiben die Roh-Clips unangetastet."""
    if not input_paths:
        raise StitchError("Keine Eingabe-Clips zum Zusammenfuegen uebergeben.")

    if shutil.which(FFMPEG_BINARY) is None:
        raise StitchError(
            f"ffmpeg-Binary '{FFMPEG_BINARY}' wurde nicht gefunden. Auf Railway "
            "muss ffmpeg explizit ueber nixpacks.toml angefordert werden -- siehe "
            "README, Abschnitt 'FFmpeg auf Railway'."
        )
    if shutil.which(FFPROBE_BINARY) is None:
        raise StitchError(
            f"ffprobe-Binary '{FFPROBE_BINARY}' wurde nicht gefunden (wird normalerweise "
            "zusammen mit ffmpeg installiert)."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [FFMPEG_BINARY, "-y"]
    for p in input_paths:
        cmd += ["-i", str(p)]

    filter_parts: list[str] = []
    concat_pairs: list[str] = []
    next_input_index = len(input_paths)

    for i, p in enumerate(input_paths):
        filter_parts.append(
            f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
            f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v{i}];"
        )
        if await _probe_has_audio(p):
            audio_label = f"{i}:a"
        else:
            duration = await _probe_duration(p)
            duration = duration if duration > 0 else 1.0
            cmd += [
                "-f", "lavfi",
                "-t", f"{duration:.3f}",
                "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            ]
            audio_label = f"{next_input_index}:a"
            next_input_index += 1
        concat_pairs.append(f"[v{i}][{audio_label}]")

    filter_complex = (
        "".join(filter_parts)
        + "".join(concat_pairs)
        + f"concat=n={len(input_paths)}:v=1:a=1[outv][outa]"
    )

    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(output_path),
    ]

    returncode, _, stderr = await _run(cmd)
    if returncode != 0 or not output_path.is_file():
        raise StitchError(
            f"ffmpeg ist mit Exit-Code {returncode} fehlgeschlagen: "
            f"{stderr.decode(errors='replace')[-800:]}"
        )

    for p in input_paths:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            logger.warning("Konnte Roh-Clip nicht loeschen: %s", p, exc_info=True)
