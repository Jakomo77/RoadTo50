import asyncio
import logging
import uuid

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from . import ai, db, plan, publish_instagram, storage
from .config import settings
from .storage import video_path_for, public_video_url

logger = logging.getLogger(__name__)

# Wie lange nach dem letzten empfangenen Clip gewartet wird, bevor die Serie als
# vollstaendig gilt und zusammengefuegt wird. Deckt sowohl echte Telegram-
# Mediengruppen (alle Items treffen praktisch gleichzeitig ein) als auch schnell
# nacheinander einzeln gesendete Clips ab (z.B. Mehrfachauswahl -> "Senden").
CLIP_BATCH_DEBOUNCE_SECONDS = 4.0

# Schutz gegen ausufernde ffmpeg-Jobs, falls versehentlich eine grosse Anzahl
# Videos in Folge geschickt wird.
MAX_CLIPS_PER_BATCH = 6

_FINALIZE_JOB_PREFIX = "finalize_clip_batch_"


def _authorized(update: Update) -> bool:
    """Der Bot reagiert nur auf deinen eigenen Chat (TELEGRAM_CHAT_ID) -- so kann
    niemand sonst deinen Account fernsteuern, selbst wenn der Bot-Token bekannt wird."""
    if not settings.telegram_chat_id:
        return True  # noch nicht konfiguriert -> waehrend lokalem Testen offen lassen
    return str(update.effective_chat.id) == str(settings.telegram_chat_id)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        await update.message.reply_text("Dieser Bot ist privat.")
        return
    idea = plan.todays_summary()
    days = plan.days_left()
    msg = f"Road to 50 Bot ist online. Noch {days} Tage bis zum 50. Geburtstag.\n\n"
    if idea:
        msg += f"Heutige Idee laut Plan: {idea}\n\n"
    msg += "Schick mir einfach dein Roh-Video, ich frage danach kurz nach, worum es geht."
    await update.message.reply_text(msg)


async def briefing_text() -> str:
    idea = plan.todays_summary()
    days = plan.days_left()
    return ai.morning_briefing(idea, days)


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nimmt einen einzelnen Clip entgegen und sammelt ihn in einer pro-Chat
    Serie (context.chat_data). Die Serie wird erst nach CLIP_BATCH_DEBOUNCE_SECONDS
    ohne weiteren eingehenden Clip als vollstaendig betrachtet und dann in
    _finalize_clip_batch() zusammengefuegt -- das deckt sowohl echte Telegram-
    Mediengruppen (media_group_id) als auch mehrere einzeln, aber kurz
    hintereinander gesendete Videos ab, ohne dass zwischen beiden Faellen
    unterschieden werden muss.

    Die Sammel-Liste liegt bewusst nur im Prozessspeicher (context.chat_data),
    nicht in der DB: Es geht um ein Zeitfenster von wenigen Sekunden, nicht um
    einen mehrstufigen Dialog wie bei pending_action -- ein Neustart genau in
    diesem kurzen Fenster ist ein vertretbares Restrisiko (der Nutzer muesste im
    Zweifel die Serie erneut schicken)."""
    if not _authorized(update):
        return
    video = update.message.video or update.message.video_note
    if video is None:
        return
    chat_id = update.effective_chat.id

    batch = context.chat_data.setdefault("clip_batch", {"raw_paths": [], "file_ids": [], "batch_id": None})
    if batch["batch_id"] is None:
        batch["batch_id"] = uuid.uuid4().hex[:12]

    index = len(batch["raw_paths"])
    if index >= MAX_CLIPS_PER_BATCH:
        await update.message.reply_text(
            f"Maximal {MAX_CLIPS_PER_BATCH} Clips pro Serie -- dieser Clip wird nicht "
            "mitgenommen. Schick den Rest bitte als neue Serie."
        )
        return

    tg_file = await context.bot.get_file(video.file_id)
    raw_path = storage.raw_clip_path(batch["batch_id"], index)
    await tg_file.download_to_drive(custom_path=str(raw_path))
    batch["raw_paths"].append(raw_path)
    batch["file_ids"].append(video.file_id)

    await update.message.reply_text(f"Clip {len(batch['raw_paths'])} empfangen...")

    # Debounce: einen evtl. schon laufenden Finalisierungs-Job fuer diesen Chat
    # verwerfen und neu terminieren, damit erst nach einer Pause ohne weiteren
    # Clip zusammengefuegt wird.
    job_name = f"{_FINALIZE_JOB_PREFIX}{chat_id}"
    for job in context.job_queue.get_jobs_by_name(job_name):
        job.schedule_removal()
    context.job_queue.run_once(
        _finalize_clip_batch,
        CLIP_BATCH_DEBOUNCE_SECONDS,
        chat_id=chat_id,
        name=job_name,
    )


async def _finalize_clip_batch(context: ContextTypes.DEFAULT_TYPE):
    """Wird CLIP_BATCH_DEBOUNCE_SECONDS nach dem letzten Clip einer Serie
    ausgefuehrt: legt den Video-Post an, fuegt bei mehreren Clips per FFmpeg
    zusammen (storage.stitch_videos) und fragt Jakob nach der Beschreibung."""
    chat_id = context.job.chat_id
    batch = context.chat_data.pop("clip_batch", None)
    if not batch or not batch["raw_paths"]:
        return

    raw_paths = batch["raw_paths"]
    batch_id = batch["batch_id"]
    clip_count = len(raw_paths)

    post = await db.create_video_post(
        telegram_file_id=",".join(batch["file_ids"]),
        user_note="",
        chat_id=str(chat_id),
    )
    await db.set_clip_count(post.id, clip_count)
    final_path = video_path_for(post.id)

    try:
        if clip_count == 1:
            # Einzelclip: kein ffmpeg-Umweg noetig, einfach an den finalen Pfad verschieben.
            raw_paths[0].rename(final_path)
        else:
            await storage.stitch_videos(raw_paths, final_path)
    except storage.StitchError:
        logger.exception("Clip-Stitching fehlgeschlagen (post_id=%s, clips=%d)", post.id, clip_count)
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"Fehler beim Zusammenfuegen der {clip_count} Clips zu einem Reel. "
                "Bitte die Serie erneut hochladen."
            ),
        )
        return
    finally:
        storage.cleanup_raw_batch_dir(batch_id)

    await db.set_video_path(post.id, str(final_path))

    if clip_count > 1:
        prompt = (
            f"{clip_count} Clips zu einem Reel zusammengefuegt. Beschreib mir kurz JEDEN "
            "Clip einzeln, z.B. 'Clip 1: Sturz, Clip 2: 8 Sekunden gehalten'."
        )
    else:
        prompt = (
            "Video empfangen. Beschreib mir kurz in 1-2 Saetzen, was zu sehen ist "
            "(z.B. 'Handstand-Versuch an der Wand, 3. Sturz, danach 10 Sek. gehalten')."
        )
    await context.bot.send_message(chat_id=chat_id, text=prompt)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    chat_id = update.effective_chat.id

    # Ersetzt das fruehere In-Memory-Dict PENDING_ACTION: der offene Vorgang wird
    # aus der DB gelesen und ueberlebt damit einen Railway-Neustart.
    post = await db.get_pending_post(str(chat_id))
    if not post:
        return  # kein offener Vorgang -> Text ignorieren (oder spaeter: freier Chat-Modus)

    text = update.message.text

    if post.pending_action == "awaiting_description":
        idea = plan.todays_summary()
        entries = plan.todays_entries()
        plan_kategorie = entries[0]["Matrix-Kategorie"] if entries else None
        skill_focus = entries[0]["Skill-Fokus"] if entries else None
        await update.message.reply_text("Alles klar, ich erstelle den Vorschlag...")
        try:
            result = ai.generate_caption(
                text,
                todays_plan_idea=idea,
                plan_kategorie=plan_kategorie,
                skill_focus=skill_focus,
                clip_count=post.clip_count or 1,
            )
        except ai.CaptionGenerationError:
            logger.exception("Caption-Generierung fehlgeschlagen (post_id=%s)", post.id)
            await update.message.reply_text(
                "Die KI konnte kein gueltiges Posting erzeugen. Bitte beschreib den "
                "Clip nochmal, ggf. etwas anders formuliert."
            )
            return
        await db.update_post_ai_result(
            post.id, result["hook"], result["caption"], result.get("hashtags", []), result["matrix_kategorie"]
        )
        await db.set_pending_action(post.id, None)
        await send_preview(update, context, post.id)

    elif post.pending_action == "awaiting_revision":
        previous = {
            "hook": post.hook,
            "caption": post.caption,
            "hashtags": post.hashtags,
            "matrix_kategorie": post.matrix_kategorie,
        }
        await update.message.reply_text("Ueberarbeite...")
        try:
            result = ai.revise_caption(previous, text)
        except ai.CaptionGenerationError:
            logger.exception("Caption-Ueberarbeitung fehlgeschlagen (post_id=%s)", post.id)
            await update.message.reply_text(
                "Die KI konnte die Ueberarbeitung nicht sauber erzeugen. Bitte "
                "formuliere dein Feedback nochmal."
            )
            return
        await db.update_post_ai_result(
            post.id, result["hook"], result["caption"], result.get("hashtags", []), result["matrix_kategorie"]
        )
        await db.set_pending_action(post.id, None)
        await send_preview(update, context, post.id)


async def send_preview(update: Update, context: ContextTypes.DEFAULT_TYPE, post_id: int):
    post = await db.get_post(post_id)
    hashtags = " ".join(f"#{h.lstrip('#')}" for h in (post.hashtags or []))
    preview = (
        f"Vorschau (Kategorie: {post.matrix_kategorie})\n\n"
        f"Hook: {post.hook}\n\n"
        f"{post.caption}\n\n"
        f"{hashtags}"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Freigeben & Posten", callback_data=f"approve:{post_id}"),
                InlineKeyboardButton("Ueberarbeiten", callback_data=f"revise:{post_id}"),
            ]
        ]
    )
    await update.message.reply_text(preview, reply_markup=keyboard)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action, post_id_str = query.data.split(":")
    post_id = int(post_id_str)

    if action == "approve":
        await db.set_status(post_id, "approved")
        post = await db.get_post(post_id)
        await query.edit_message_text(query.message.text + "\n\n-- Freigegeben.")

        # YouTube-Anbindung folgt separat (siehe README, Schritt 3) -- hier zunaechst
        # nur Instagram, wie im Briefing fuer diese Iteration vereinbart.
        # Existenzpruefung: video_path in der DB kann auf eine Datei zeigen, die
        # nach einem Railway-Redeploy (nicht-persistentes Dateisystem) nicht mehr
        # da ist -- deshalb zusaetzlich zum DB-Feld die Datei selbst pruefen.
        if not post.video_path or not storage.video_exists(post_id):
            await query.message.reply_text("Videodatei nicht gefunden, bitte erneut hochladen.")
            return

        try:
            video_url = public_video_url(post_id)
        except RuntimeError as e:
            await query.message.reply_text(f"{e}\n\nVideo bitte manuell posten.")
            return

        caption_text = (post.hook or "") + "\n\n" + (post.caption or "")
        hashtags = " ".join(f"#{h.lstrip('#')}" for h in (post.hashtags or []))
        if hashtags:
            caption_text += "\n\n" + hashtags

        await query.message.reply_text("Starte Instagram-Upload...")
        try:
            # publish_reel() macht blockierende HTTP-Requests -- in einen Thread
            # auslagern, damit der Bot waehrenddessen nicht einfriert.
            media_id = await asyncio.to_thread(publish_instagram.publish_reel, video_url, caption_text)
        except publish_instagram.InstagramNotConfigured as e:
            await db.set_ig_status(post_id, "not_configured")
            await query.message.reply_text(f"{e}\n\nVideo bitte manuell posten.")
        except Exception as e:
            logger.exception("Instagram-Publish fehlgeschlagen (post_id=%s)", post_id)
            await db.set_status(post_id, "failed")
            await db.set_ig_status(post_id, "failed")
            await query.message.reply_text(
                f"Instagram-Upload fehlgeschlagen: {e}\n\nVideo bitte manuell posten."
            )
        else:
            await db.set_status(post_id, "posted_ig")
            await db.set_ig_status(post_id, f"posted:{media_id}")
            # Lokalen Clip nach erfolgreichem Publish loeschen, damit der
            # (begrenzte) Railway-Speicher nicht vollaeuft.
            storage.cleanup_video(post_id)
            await db.set_video_path(post_id, None)
            await query.message.reply_text(
                f"Auf Instagram veroeffentlicht (media_id {media_id}). Lokale Videodatei wurde geloescht."
            )
    elif action == "revise":
        await db.set_pending_action(post_id, "awaiting_revision")
        await query.message.reply_text("Was soll ich aendern?")


def build_application() -> Application:
    application = ApplicationBuilder().token(settings.telegram_bot_token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.VIDEO | filters.VIDEO_NOTE, handle_video))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(CallbackQueryHandler(handle_callback))
    return application
