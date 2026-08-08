import datetime
from sqlalchemy import String, Integer, DateTime, JSON, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from .config import settings


class Base(DeclarativeBase):
    pass


class VideoPost(Base):
    """Ein Roh-Clip, den Jakob per Telegram geschickt hat, mitsamt KI-generierter
    Caption und dem aktuellen Freigabe-/Veroeffentlichungs-Status."""

    __tablename__ = "video_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )
    telegram_file_id: Mapped[str] = mapped_column(String, nullable=True)
    video_path: Mapped[str] = mapped_column(String, nullable=True)  # lokaler Pfad des heruntergeladenen Clips (static/videos/{id}.mp4)
    user_note: Mapped[str] = mapped_column(String, nullable=True)  # Jakobs kurze Beschreibung des Clips

    # Telegram-Chat, zu dem dieser Post gehoert -- noetig, um pending_action ohne
    # In-Memory-State aufzuloesen (siehe get_pending_post()).
    chat_id: Mapped[str] = mapped_column(String, nullable=True)

    hook: Mapped[str] = mapped_column(String, nullable=True)
    caption: Mapped[str] = mapped_column(String, nullable=True)
    hashtags: Mapped[list] = mapped_column(JSON, nullable=True)
    matrix_kategorie: Mapped[str] = mapped_column(String, nullable=True)

    # Status: draft -> awaiting_approval -> approved -> posted_ig / posted_yt -> failed
    status: Mapped[str] = mapped_column(String, default="draft")
    ig_status: Mapped[str] = mapped_column(String, default="not_posted")
    yt_status: Mapped[str] = mapped_column(String, default="not_posted")

    # Worauf der Bot bei der naechsten Text-Nachricht in diesem Chat wartet:
    # 'awaiting_description' | 'awaiting_revision' | None (kein offener Vorgang).
    # Persistiert in der DB statt in einem In-Memory-Dict, damit ein Railway-
    # Neustart mitten im Dialog den Zustand nicht verliert.
    pending_action: Mapped[str] = mapped_column(String, nullable=True)

    # Anzahl der per FFmpeg zu diesem Post zusammengefuegten Roh-Clips (1 =
    # normaler Einzelclip, kein Stitching noetig). Wird u.a. genutzt, um Claude
    # beim Caption-Generieren mitzuteilen, dass es sich um eine Clip-Sequenz
    # handelt (siehe ai.py::generate_caption).
    clip_count: Mapped[int] = mapped_column(Integer, default=1)


# echo=False haelt die Logs sauber; bei Bedarf zum Debuggen auf True stellen
engine = create_async_engine(settings.database_url, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def create_video_post(telegram_file_id: str, user_note: str, chat_id: str) -> VideoPost:
    async with async_session() as session:
        post = VideoPost(
            telegram_file_id=telegram_file_id,
            user_note=user_note,
            chat_id=chat_id,
            status="draft",
            pending_action="awaiting_description",
        )
        session.add(post)
        await session.commit()
        await session.refresh(post)
        return post


async def update_post_ai_result(post_id: int, hook: str, caption: str, hashtags: list, matrix_kategorie: str):
    async with async_session() as session:
        post = await session.get(VideoPost, post_id)
        post.hook = hook
        post.caption = caption
        post.hashtags = hashtags
        post.matrix_kategorie = matrix_kategorie
        post.status = "awaiting_approval"
        await session.commit()


async def get_post(post_id: int) -> VideoPost | None:
    async with async_session() as session:
        return await session.get(VideoPost, post_id)


async def set_status(post_id: int, status: str):
    async with async_session() as session:
        post = await session.get(VideoPost, post_id)
        post.status = status
        await session.commit()


async def set_video_path(post_id: int, video_path: str):
    async with async_session() as session:
        post = await session.get(VideoPost, post_id)
        post.video_path = video_path
        await session.commit()


async def set_ig_status(post_id: int, ig_status: str):
    async with async_session() as session:
        post = await session.get(VideoPost, post_id)
        post.ig_status = ig_status
        await session.commit()


async def set_pending_action(post_id: int, pending_action: str | None):
    """Setzt/loescht, worauf der Bot bei der naechsten Text-Nachricht dieses
    Chats wartet. pending_action=None markiert den Vorgang als abgeschlossen."""
    async with async_session() as session:
        post = await session.get(VideoPost, post_id)
        post.pending_action = pending_action
        await session.commit()


async def set_clip_count(post_id: int, clip_count: int):
    async with async_session() as session:
        post = await session.get(VideoPost, post_id)
        post.clip_count = clip_count
        await session.commit()


async def get_pending_post(chat_id: str) -> VideoPost | None:
    """Findet den juengsten Video-Post dieses Chats, auf dessen Text-Antwort der
    Bot noch wartet (pending_action gesetzt). Ersetzt das fruehere In-Memory-Dict
    PENDING_ACTION -- der Zustand liegt jetzt in der DB und ueberlebt einen
    Railway-Neustart."""
    async with async_session() as session:
        result = await session.execute(
            select(VideoPost)
            .where(VideoPost.chat_id == chat_id, VideoPost.pending_action.is_not(None))
            .order_by(VideoPost.id.desc())
            .limit(1)
        )
        return result.scalars().first()
