from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer

from .config import get_settings

settings = get_settings()


# ---------- auth ----------


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    username: str
    display_name: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str
    role: str


# ---------- rounds / manuscripts ----------


class RoundCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class ManuscriptIn(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    abstract: str = ""
    author_name: str = Field(min_length=1, max_length=128)
    revision_no: int = Field(default=1, ge=1, le=9999)


class ManuscriptAnonOut(BaseModel):
    """What reviewers may see before the round is frozen: never includes author."""

    model_config = ConfigDict(from_attributes=True)

    manuscript_no: int
    title: str
    abstract: str
    revision_no: int


class ManuscriptAdminOut(ManuscriptAnonOut):
    id: int
    author_name: str


class RoundSummaryOut(BaseModel):
    id: int
    name: str
    is_frozen: bool
    frozen_at: dt.datetime | None
    manuscript_count: int
    reviewer_count: int
    assignment_count: int
    submitted_count: int


class RoundDetailOut(RoundSummaryOut):
    manuscripts: list[ManuscriptAdminOut]
    reviewers: list[UserOut]
    conflicts: list["ConflictOut"]
    assignments: list["AssignmentAdminOut"]


# ---------- conflicts ----------


class ConflictIn(BaseModel):
    reviewer_id: int
    manuscript_no: int = Field(ge=1)
    reason: str = Field(default="", max_length=256)


class ConflictOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    reviewer_id: int
    manuscript_no: int
    reason: str


# ---------- assignments ----------


class AssignmentItemIn(BaseModel):
    reviewer_id: int
    manuscript_no: int = Field(ge=1)


class AssignmentReplaceIn(BaseModel):
    assignments: list[AssignmentItemIn]

    @field_validator("assignments")
    @classmethod
    def _enforce_limits(cls, v: list[AssignmentItemIn]) -> list[AssignmentItemIn]:
        if not v:
            return v
        # distinct reviewers in one round <= 10
        reviewers = {item.reviewer_id for item in v}
        if len(reviewers) > settings.max_reviewers_per_round:
            raise ValueError(
                f"a round involves at most {settings.max_reviewers_per_round} reviewers"
            )
        # per-reviewer load <= 3
        per_reviewer: dict[int, int] = {}
        for item in v:
            per_reviewer[item.reviewer_id] = per_reviewer.get(item.reviewer_id, 0) + 1
            if per_reviewer[item.reviewer_id] > settings.max_assignments_per_reviewer:
                raise ValueError(
                    f"a reviewer receives at most "
                    f"{settings.max_assignments_per_reviewer} assignments"
                )
        # no duplicate (reviewer, manuscript) pairs
        pairs = [(item.reviewer_id, item.manuscript_no) for item in v]
        if len(set(pairs)) != len(pairs):
            raise ValueError("duplicate reviewer/manuscript pairs are not allowed")
        return v


class AssignmentAdminOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    reviewer_id: int
    manuscript_no: int
    submitted: bool
    submitted_at: dt.datetime | None = None


class AssignmentReviewerOut(BaseModel):
    """Reviewer-facing assignment. Author fields are added only after reveal."""

    assignment_id: int
    round_id: int
    manuscript_no: int
    revision_no: int
    title: str
    abstract: str
    submitted: bool
    submitted_at: dt.datetime | None = None
    author_name: str | None = None

    @model_serializer(mode="plain")
    def _serialize(self):
        data = {
            "assignment_id": self.assignment_id,
            "round_id": self.round_id,
            "manuscript_no": self.manuscript_no,
            "revision_no": self.revision_no,
            "title": self.title,
            "abstract": self.abstract,
            "submitted": self.submitted,
            "submitted_at": self.submitted_at,
        }
        # The key itself exists only after reveal; never emit author_name: null.
        if self.author_name is not None:
            data["author_name"] = self.author_name
        return data


# ---------- drafts ----------


class DraftIn(BaseModel):
    revision_no: int = Field(ge=1, le=9999)
    score: int | None = Field(default=None, ge=0, le=100)
    comment: str = Field(default="", max_length=100_000)
    expected_version: int | None = Field(default=None, ge=1)


class DraftSubmitIn(BaseModel):
    revision_no: int = Field(ge=1, le=9999)
    score: int | None = Field(default=None, ge=0, le=100)
    comment: str = Field(default="", max_length=100_000)


class DraftOut(BaseModel):
    revision_no: int
    score: int | None
    comment: str
    version: int
    updated_at: dt.datetime | None


# ---------- reviewer-facing projections ----------


class ManuscriptNumberOut(BaseModel):
    """Pre-freeze: only the anonymous number (+ current revision). No title/author."""

    manuscript_no: int
    revision_no: int


class ManuscriptRevealedOut(BaseModel):
    """Post-freeze: author identity is revealed."""

    manuscript_no: int
    revision_no: int
    title: str
    abstract: str
    author_name: str


class AssignmentDetailOut(BaseModel):
    assignment_id: int
    round_id: int
    manuscript_no: int
    revision_no: int
    title: str
    abstract: str
    submitted: bool
    submitted_at: dt.datetime | None = None
    author_name: str | None = None
    drafts: list[DraftOut] = []

    @model_serializer(mode="plain")
    def _serialize(self):
        data = {
            "assignment_id": self.assignment_id,
            "round_id": self.round_id,
            "manuscript_no": self.manuscript_no,
            "revision_no": self.revision_no,
            "title": self.title,
            "abstract": self.abstract,
            "submitted": self.submitted,
            "submitted_at": self.submitted_at,
            "drafts": [d.model_dump() for d in self.drafts],
        }
        if self.author_name is not None:
            data["author_name"] = self.author_name
        return data


class RoundReviewerOut(BaseModel):
    id: int
    name: str
    is_frozen: bool
    frozen_at: dt.datetime | None = None
    manuscripts: list[dict]
    assignments: list[AssignmentReviewerOut]


RoundDetailOut.model_rebuild()
