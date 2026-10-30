from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _now() -> datetime:
    return datetime.now(UTC)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    items: Mapped[list["Item"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Item(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="todo")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    project: Mapped[Project] = relationship(back_populates="items")
