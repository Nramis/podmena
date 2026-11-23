import hashlib
import json
import os
from typing import Optional
import secrets
from datetime import datetime, timezone
from pathlib import Path

from argon2 import PasswordHasher
from cryptography.fernet import Fernet
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text
from sqlalchemy import ForeignKeyConstraint, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def utcnow():
    return datetime.now(timezone.utc)


def new_id():
    return secrets.token_hex(16)


def secret(name):
    path = os.getenv(f"{name}_FILE")
    value = Path(path).read_text().strip() if path else os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Set {name}_FILE or {name}")
    return value


def database_url():
    if url := os.getenv("DATABASE_URL"):
        return url
    from sqlalchemy import URL

    return URL.create("postgresql+psycopg", username=os.getenv("POSTGRES_USER", "podmena"),
                      password=secret("POSTGRES_PASSWORD"), host=os.getenv("POSTGRES_HOST", "db"),
                      database=os.getenv("POSTGRES_DB", "podmena"))


def make_sessions(url=None):
    engine = create_engine(url or database_url(), pool_pre_ping=True, hide_parameters=True)
    return sessionmaker(engine, expire_on_commit=False)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


passwords = PasswordHasher()


class Cipher:
    def __init__(self, key):
        self.fernet = Fernet(key)

    def pack(self, obj):
        return self.fernet.encrypt(json.dumps(obj, ensure_ascii=False).encode())

    def unpack(self, obj):
        return json.loads(self.fernet.decrypt(obj))


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class LoginSession(Base):
    __tablename__ = "login_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Matrix(Base):
    __tablename__ = "matrices"
    __table_args__ = (UniqueConstraint("owner", "name"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    latest: Mapped[int] = mapped_column(Integer, default=0)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Version(Base):
    __tablename__ = "matrix_versions"

    matrix_id: Mapped[str] = mapped_column(ForeignKey("matrices.id", ondelete="CASCADE"), primary_key=True)
    number: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[bytes] = mapped_column(LargeBinary)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class History(Base):
    __tablename__ = "history"
    __table_args__ = (
        ForeignKeyConstraint(
            ("matrix_id", "version"), ("matrix_versions.matrix_id", "matrix_versions.number")
        ),
        Index("ix_history_owner_created", "owner", "created"),
        Index("ix_history_created", "created"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(24))
    matrix_id: Mapped[str] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer)
    changed: Mapped[int] = mapped_column(Integer)
    label: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
