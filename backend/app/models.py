from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # "admin" | "reviewer"
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Round(Base):
    __tablename__ = "rounds"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_frozen: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    frozen_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    manuscripts: Mapped[list[Manuscript]] = relationship(
        back_populates="round", cascade="all, delete-orphan"
    )
    assignments: Mapped[list[Assignment]] = relationship(
        back_populates="round", cascade="all, delete-orphan"
    )
    conflicts: Mapped[list[Conflict]] = relationship(
        back_populates="round", cascade="all, delete-orphan"
    )


class Manuscript(Base):
    __tablename__ = "manuscripts"
    __table_args__ = (UniqueConstraint("round_id", "manuscript_no", name="uq_manuscript_round_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id", ondelete="CASCADE"), index=True)
    manuscript_no: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    abstract: Mapped[str] = mapped_column(Text, default="", nullable=False)
    author_name: Mapped[str] = mapped_column(String(128), nullable=False)
    revision_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    round: Mapped[Round] = relationship(back_populates="manuscripts")


class Conflict(Base):
    """COI entry: reviewer must not be assigned this manuscript in this round."""

    __tablename__ = "conflicts"
    __table_args__ = (
        UniqueConstraint("round_id", "reviewer_id", "manuscript_no", name="uq_conflict_triple"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id", ondelete="CASCADE"), index=True)
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    manuscript_no: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(256), default="", nullable=False)

    round: Mapped[Round] = relationship(back_populates="conflicts")


class Assignment(Base):
    __tablename__ = "assignments"
    __table_args__ = (
        UniqueConstraint("round_id", "reviewer_id", "manuscript_no", name="uq_assignment_triple"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id", ondelete="CASCADE"), index=True)
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    manuscript_no: Mapped[int] = mapped_column(Integer, nullable=False)
    submitted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    submitted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    round: Mapped[Round] = relationship(back_populates="assignments")
    reviewer: Mapped[User] = relationship()
    drafts: Mapped[list[Draft]] = relationship(
        back_populates="assignment", cascade="all, delete-orphan"
    )


class Draft(Base):
    """One draft per (assignment, revision_no); latest_revision selects the current one."""

    __tablename__ = "drafts"
    __table_args__ = (
        UniqueConstraint("assignment_id", "revision_no", name="uq_draft_assignment_revision"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int] = mapped_column(
        ForeignKey("assignments.id", ondelete="CASCADE"), index=True
    )
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # optimistic-concurrency token, bumped on every content update
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    assignment: Mapped[Assignment] = relationship(back_populates="drafts")
