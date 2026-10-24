from datetime import datetime, timezone
from sqlalchemy import JSON, Integer, String, Text, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

class Base(DeclarativeBase):
    pass

class Recipe(Base):
    __tablename__ = 'recipes'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String, index=True)
    source: Mapped[str] = mapped_column(String)
    conversation_id: Mapped[str | None] = mapped_column(String, nullable=True)
    content: Mapped[dict] = mapped_column(JSON)
    search_title: Mapped[str] = mapped_column(String)
    total_minutes: Mapped[int] = mapped_column(Integer, index=True)
    active_minutes: Mapped[int] = mapped_column(Integer, index=True)

class Product(Base):
    __tablename__ = 'products'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    normalized: Mapped[str] = mapped_column(String, unique=True)

class IngredientLink(Base):
    __tablename__ = 'recipe_ingredients'
    recipe_id: Mapped[str] = mapped_column(String, primary_key=True)
    local_id: Mapped[str] = mapped_column(String, primary_key=True)
    product_id: Mapped[str] = mapped_column(String, index=True)

class Conversation(Base):
    __tablename__ = 'conversations'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    recipe_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)

class Message(Base):
    __tablename__ = 'messages'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String, index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    reply: Mapped[dict | None] = mapped_column(JSON, nullable=True)

class Generation(Base):
    __tablename__ = 'generation_requests'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String, index=True)
    fingerprint: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    reply: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)

class Draft(Base):
    __tablename__ = 'recipe_drafts'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String, index=True)
    content: Mapped[dict] = mapped_column(JSON)
    base_recipe_id: Mapped[str | None] = mapped_column(String, nullable=True)
    base_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    saved_id: Mapped[str | None] = mapped_column(String, nullable=True)
    saved_target: Mapped[str | None] = mapped_column(String, nullable=True)

class LoginSession(Base):
    __tablename__ = 'sessions'
    token_hash: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer)
    expires: Mapped[int] = mapped_column(Integer)

class Idempotency(Base):
    __tablename__ = 'idempotency_records'
    key: Mapped[str] = mapped_column(String, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String)
    result: Mapped[dict] = mapped_column(JSON)
    status: Mapped[int] = mapped_column(Integer)

def database(url):
    engine = create_engine(url, connect_args={'check_same_thread': False, 'timeout': 10} if url.startswith('sqlite') else {})
    if url.startswith('sqlite'):
        @event.listens_for(engine, 'connect')
        def pragmas(connection, _):
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('PRAGMA journal_mode=WAL')
    return engine, sessionmaker(engine, expire_on_commit=False)
