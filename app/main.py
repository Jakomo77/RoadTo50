import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from telegram import Update

from . import db, telegram_bot
from .config import settings
from .storage import STATIC_DIR

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

telegram_app = telegram_bot.build_application()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    await telegram_app.initialize()
    if settings.webhook_base_url:
        webhook_url = f"{settings.webhook_base_url.rstrip('/')}/telegram/webhook"
        await telegram_app.bot.set_webhook(url=webhook_url)
        logger.info("Telegram-Webhook gesetzt: %s", webhook_url)
    await telegram_app.start()
    yield
    await telegram_app.stop()
    await telegram_app.shutdown()


app = FastAPI(lifespan=lifespan)

# Macht heruntergeladene Telegram-Clips unter {WEBHOOK_BASE_URL}/static/videos/{id}.mp4
# oeffentlich erreichbar -- das ist die Pflicht-'video_url' fuer den Instagram-Publish
# (siehe app/storage.py und app/publish_instagram.py).
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}


@app.post("/cron/morning-briefing")
async def morning_briefing(secret: str):
    """Wird taeglich um 06:00 Uhr von Railways Cron-Job aufgerufen (siehe README).
    Schickt Jakob das KI-generierte Morgen-Briefing per Telegram."""
    if secret != settings.cron_secret:
        raise HTTPException(status_code=403, detail="invalid secret")
    if not settings.telegram_chat_id:
        raise HTTPException(status_code=500, detail="TELEGRAM_CHAT_ID nicht gesetzt")

    text = await telegram_bot.briefing_text()
    await telegram_app.bot.send_message(chat_id=settings.telegram_chat_id, text=text)
    return {"sent": True}
