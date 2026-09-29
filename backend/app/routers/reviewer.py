from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..models import Assignment, Draft, Manuscript, Round, User
from ..schemas import (
    AssignmentDetailOut,
    AssignmentReviewerOut,
    DraftIn,
    DraftOut,
    DraftSubmitIn,
    ManuscriptNumberOut,
    ManuscriptRevealedOut,
    RoundReviewerOut,
)
from ..security import require_reviewer

router = APIRouter(prefix="/api/reviewer", tags=["reviewer"], dependencies=[Depends(require_reviewer)])


def _owned_assignment(db: Session, assignment_id: int, user: User) -> Assignment:
    """Fetch an assignment or raise 404.

    Wrong owner and missing id produce the same 404 deliberately: existence is
    itself information that must not leak across reviewers.
    """
    assignment = db.scalar(
        select(Assignment)
        .where(Assignment.id == assignment_id, Assignment.reviewer_id == user.id)
        .options(selectinload(Assignment.drafts))
    )
    if assignment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="assignment not found")
    return assignment


def _manuscript_by_no(db: Session, round_id: int, no: int) -> Manuscript | None:
    return db.scalar(
        select(Manuscript).where(
            Manuscript.round_id == round_id, Manuscript.manuscript_no == no
        )
    )


def _reviewer_assignment_out(
    db: Session, round_obj: Round, assignment: Assignment
) -> AssignmentReviewerOut:
    manuscript = _manuscript_by_no(db, round_obj.id, assignment.manuscript_no)
    if manuscript is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="manuscript missing")
    return AssignmentReviewerOut(
        assignment_id=assignment.id,
        round_id=round_obj.id,
        manuscript_no=assignment.manuscript_no,
        revision_no=manuscript.revision_no,
        title=manuscript.title,
        abstract=manuscript.abstract,
        submitted=assignment.submitted,
        submitted_at=assignment.submitted_at,
        # author identity exists in the payload only after the round is frozen
        author_name=manuscript.author_name if round_obj.is_frozen else None,
    )


# ---------- rounds / manuscripts (read) ----------


@router.get("/rounds", response_model=list[RoundReviewerOut])
def my_rounds(db: Session = Depends(get_db), user: User = Depends(require_reviewer)):
    round_ids = db.scalars(
        select(Assignment.round_id)
        .where(Assignment.reviewer_id == user.id)
        .distinct()
    ).all()
    result: list[RoundReviewerOut] = []
    for rid in round_ids:
        round_obj = db.get(Round, rid)
        if round_obj is None:
            continue
        result.append(_round_projection(db, round_obj, user))
    return result


def _round_projection(db: Session, round_obj: Round, user: User) -> RoundReviewerOut:
    # All manuscripts in the round are visible as anonymous numbers only.
    manuscripts: list[dict]
    if round_obj.is_frozen:
        rows = db.scalars(
            select(Manuscript)
            .where(Manuscript.round_id == round_obj.id)
            .order_by(Manuscript.manuscript_no)
        ).all()
        manuscripts = [
            ManuscriptRevealedOut(
                manuscript_no=m.manuscript_no,
                revision_no=m.revision_no,
                title=m.title,
                abstract=m.abstract,
                author_name=m.author_name,
            ).model_dump()
            for m in rows
        ]
    else:
        rows = db.execute(
            select(Manuscript.manuscript_no, Manuscript.revision_no)
            .where(Manuscript.round_id == round_obj.id)
            .order_by(Manuscript.manuscript_no)
        ).all()
        manuscripts = [
            ManuscriptNumberOut(manuscript_no=no, revision_no=rev).model_dump()
            for no, rev in rows
        ]

    assignments = list(
        db.scalars(
            select(Assignment).where(
                Assignment.round_id == round_obj.id, Assignment.reviewer_id == user.id
            )
        )
    )
    return RoundReviewerOut(
        id=round_obj.id,
        name=round_obj.name,
        is_frozen=round_obj.is_frozen,
        frozen_at=round_obj.frozen_at,
        manuscripts=manuscripts,
        assignments=[_reviewer_assignment_out(db, round_obj, a) for a in assignments],
    )


@router.get("/rounds/{round_id}", response_model=RoundReviewerOut)
def get_round(round_id: int, db: Session = Depends(get_db), user: User = Depends(require_reviewer)):
    # Membership check happens before we confirm the round exists.
    member = db.scalar(
        select(Assignment.id).where(
            Assignment.round_id == round_id, Assignment.reviewer_id == user.id
        )
    )
    round_obj = db.get(Round, round_id)
    if round_obj is None or member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="round not found")
    return _round_projection(db, round_obj, user)


# ---------- own assignment detail ----------


@router.get("/assignments/{assignment_id}", response_model=AssignmentDetailOut)
def get_assignment(
    assignment_id: int, db: Session = Depends(get_db), user: User = Depends(require_reviewer)
):
    assignment = _owned_assignment(db, assignment_id, user)
    round_obj = db.get(Round, assignment.round_id)
    if round_obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="round not found")
    manuscript = _manuscript_by_no(db, round_obj.id, assignment.manuscript_no)
    if manuscript is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="manuscript missing")
    return AssignmentDetailOut(
        assignment_id=assignment.id,
        round_id=round_obj.id,
        manuscript_no=assignment.manuscript_no,
        revision_no=manuscript.revision_no,
        title=manuscript.title,
        abstract=manuscript.abstract,
        submitted=assignment.submitted,
        submitted_at=assignment.submitted_at,
        author_name=manuscript.author_name if round_obj.is_frozen else None,
        drafts=[
            DraftOut(
                revision_no=d.revision_no,
                score=d.score,
                comment=d.comment,
                version=d.version,
                updated_at=d.updated_at,
            )
            for d in sorted(assignment.drafts, key=lambda x: x.revision_no)
        ],
    )


# ---------- drafts ----------


def _locked_round_and_assignment(
    db: Session, assignment_id: int, user: User
) -> tuple[Round, Assignment]:
    """Lock order: round row first, then assignment row (matches admin freeze)."""
    candidate = db.scalar(
        select(Assignment).where(Assignment.id == assignment_id, Assignment.reviewer_id == user.id)
    )
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="assignment not found")
    round_obj = db.get(Round, candidate.round_id, with_for_update=True)
    assignment = db.get(Assignment, assignment_id, with_for_update=True)
    if round_obj is None or assignment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="assignment not found")
    return round_obj, assignment


def _ensure_editable(round_obj: Round, assignment: Assignment) -> None:
    if round_obj.is_frozen:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="round is frozen")
    if assignment.submitted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="review already final-submitted"
        )


@router.put("/assignments/{assignment_id}/draft", response_model=DraftOut)
def save_draft(
    assignment_id: int,
    body: DraftIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_reviewer),
):
    # Drafts may be kept for any positive revision number; only final submit
    # must match the manuscript's current revision.
    round_obj, assignment = _locked_round_and_assignment(db, assignment_id, user)
    _ensure_editable(round_obj, assignment)

    manuscript = _manuscript_by_no(db, round_obj.id, assignment.manuscript_no)
    if manuscript is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="manuscript missing")

    draft = db.scalar(
        select(Draft).where(
            Draft.assignment_id == assignment.id, Draft.revision_no == body.revision_no
        )
    )
    if draft is None:
        # The unique constraint catches two writers creating the row at once.
        draft = Draft(
            assignment_id=assignment.id,
            revision_no=body.revision_no,
            score=body.score,
            comment=body.comment,
            version=1,
        )
        db.add(draft)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="draft was created concurrently; resend with expected_version",
            )
    elif body.expected_version is None:
        # blind save: last writer wins, but the version still advances
        draft.score = body.score
        draft.comment = body.comment
        draft.version += 1
    else:
        # atomic compare-and-swap on the version token; one statement that
        # behaves identically on PostgreSQL and SQLite, independent of row locks
        current_version = db.scalar(
            select(Draft.version).where(
                Draft.assignment_id == assignment.id,
                Draft.revision_no == body.revision_no,
            )
        )
        if body.expected_version != current_version:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "draft_version_conflict",
                    "current_version": current_version,
                },
            )
        result = db.execute(
            update(Draft)
            .where(
                Draft.assignment_id == assignment.id,
                Draft.revision_no == body.revision_no,
                Draft.version == body.expected_version,
            )
            .values(
                score=body.score,
                comment=body.comment,
                version=body.expected_version + 1,
            )
        )
        if result.rowcount != 1:
            current = db.scalar(
                select(Draft.version).where(
                    Draft.assignment_id == assignment.id,
                    Draft.revision_no == body.revision_no,
                )
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error": "draft_version_conflict", "current_version": current},
            )
    db.commit()
    draft = db.scalar(
        select(Draft).where(
            Draft.assignment_id == assignment.id, Draft.revision_no == body.revision_no
        )
    )
    return DraftOut(
        revision_no=draft.revision_no,
        score=draft.score,
        comment=draft.comment,
        version=draft.version,
        updated_at=draft.updated_at,
    )


@router.post("/assignments/{assignment_id}/submit", response_model=AssignmentReviewerOut)
def final_submit(
    assignment_id: int,
    body: DraftSubmitIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_reviewer),
):
    round_obj, assignment = _locked_round_and_assignment(db, assignment_id, user)
    _ensure_editable(round_obj, assignment)

    manuscript = _manuscript_by_no(db, round_obj.id, assignment.manuscript_no)
    if manuscript is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="manuscript missing")
    if body.revision_no != manuscript.revision_no:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"manuscript is currently at revision {manuscript.revision_no}",
        )
    if body.score is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="a score is required for final submission",
        )

    draft = db.scalar(
        select(Draft).where(
            Draft.assignment_id == assignment.id, Draft.revision_no == body.revision_no
        ).with_for_update()
    )
    if draft is None:
        draft = Draft(
            assignment_id=assignment.id,
            revision_no=body.revision_no,
            version=1,
        )
        db.add(draft)
        try:
            db.flush()
        except IntegrityError:
            # a concurrent draft save created the same (assignment, revision) row
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="review updated concurrently; please retry",
            )
    draft.score = body.score
    draft.comment = body.comment
    # final submission does not bump the optimistic-concurrency version; content
    # is sealed by assignment.submitted / round.is_frozen afterwards.
    assignment.submitted = True
    assignment.submitted_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    db.refresh(assignment)
    return _reviewer_assignment_out(db, round_obj, assignment)
