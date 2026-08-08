import os
from dataclasses import dataclass


@dataclass
class Settings:
    # Telegram
    telegram_bot_token: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    # Nur diese Chat-ID darf den Bot benutzen (deine private Telegram-Chat-ID, s. README)
    telegram_chat_id: str = os.environ.get("TELEGRAM_CHAT_ID", "")

    # KI
    anthropic_api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")

    # Datenbank (Railway setzt DATABASE_URL automatisch, wenn du ein Postgres-Addon hinzufügst)
    database_url: str = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./local.db")

    # Öffentliche URL dieses Services (z.B. https://dein-projekt.up.railway.app)
    webhook_base_url: str = os.environ.get("WEBHOOK_BASE_URL", "")

    # Geheimer Query-Parameter, der den /cron-Endpoint vor fremdem Zugriff schützt
    cron_secret: str = os.environ.get("CRON_SECRET", "change-me")

    # Instagram Graph API (Business Login for Instagram) — Schritt 2, siehe README
    ig_access_token: str = os.environ.get("IG_ACCESS_TOKEN", "")
    ig_business_account_id: str = os.environ.get("IG_BUSINESS_ACCOUNT_ID", "")

    # YouTube Data API v3 — Schritt 2, siehe README
    yt_client_secret_file: str = os.environ.get("YT_CLIENT_SECRET_FILE", "yt_client_secret.json")
    yt_token_file: str = os.environ.get("YT_TOKEN_FILE", "yt_token.json")


settings = Settings()
