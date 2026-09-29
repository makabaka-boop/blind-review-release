from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from ..config import get_settings
from ..database import get_db
from ..models import Assignment, Conflict, Manuscript, Round, User
from ..schemas import (
    AssignmentAdminOut,
    AssignmentReplaceIn,
    ConflictIn,
    ConflictOut,
    ManuscriptAdminOut,
    ManuscriptIn,
    RoundCreate,
    RoundDetailOut,
    RoundSummaryOut,
    UserOut,
)
from ..security import require_admin

settings = get_settings()
router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


# ---------- helpers ----------


def _get_round_locked(db: Session, round_id: int, *, require_open: bool = False) -> Round:
    """Load a round with a row lock so freeze/submit cannot interleave."""
    round_obj = db.get(Round, round_id, with_for_update=True)
    if round_obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="round not found")
    if require_open and round_obj.is_frozen:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="round is frozen")
    return round_obj


def _get_round(db: Session, round_id: int) -> Round:
    round_obj = db.get(Round, round_id)
    if round_obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="round not found")
    return round_obj


def _summary(db: Session, round_obj: Round) -> RoundSummaryOut:
    manuscript_count = db.scalar(
        select(func.count(Manuscript.id)).where(Manuscript.round_id == round_obj.id)
    )
    assignment_count = db.scalar(
        select(func.count(Assignment.id)).where(Assignment.round_id == round_obj.id)
    )
    submitted_count = db.scalar(
        select(func.count(Assignment.id)).where(
            Assignment.round_id == round_obj.id, Assignment.submitted.is_(True)
        )
    )
    reviewer_count = db.scalar(
        select(func.count(func.distinct(Assignment.reviewer_id))).where(
            Assignment.round_id == round_obj.id
        )
    )
    return RoundSummaryOut(
        id=round_obj.id,
        name=round_obj.name,
        is_frozen=round_obj.is_frozen,
        frozen_at=round_obj.frozen_at,
        manuscript_count=manuscript_count or 0,
        reviewer_count=reviewer_count or 0,
        assignment_count=assignment_count or 0,
        submitted_count=submitted_count or 0,
    )


def _reviewer_or_404(db: Session, reviewer_id: int) -> User:
    user = db.get(User, reviewer_id)
    if user is None or user.role != "reviewer":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="reviewer not found")
    return user


def _manuscript_nos(db: Session, round_id: int) -> set[int]:
    rows = db.scalars(select(Manuscript.manuscript_no).where(Manuscript.round_id == round_id))
    return set(rows)


# ---------- reviewers / rounds ----------


@router.get("/reviewers", response_model=list[UserOut])
def list_reviewers(db: Session = Depends(get_db)) -> list[User]:
    return list(db.scalars(select(User).where(User.role == "reviewer").order_by(User.id)))


@router.post("/rounds", response_model=RoundSummaryOut, status_code=status.HTTP_201_CREATED)
def create_round(body: RoundCreate, db: Session = Depends(get_db)) -> RoundSummaryOut:
    round_obj = Round(name=body.name)
    db.add(round_obj)
    db.commit()
    db.refresh(round_obj)
    return _summary(db, round_obj)


@router.get("/rounds", response_model=list[RoundSummaryOut])
def list_rounds(db: Session = Depends(get_db)) -> list[RoundSummaryOut]:
    rounds = list(db.scalars(select(Round).order_by(Round.id)))
    return [_summary(db, r) for r in rounds]


@router.get("/rounds/{round_id}", response_model=RoundDetailOut)
def get_round(round_id: int, db: Session = Depends(get_db)) -> RoundDetailOut:
    round_obj = db.scalar(
        select(Round)
        .where(Round.id == round_id)
        .options(
            selectinload(Round.manuscripts),
            selectinload(Round.conflicts),
            selectinload(Round.assignments),
        )
    )
    if round_obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="round not found")
    summary = _summary(db, round_obj)
    reviewer_ids = {a.reviewer_id for a in round_obj.assignments}
    reviewers = (
        list(db.scalars(select(User).where(User.id.in_(reviewer_ids)))) if reviewer_ids else []
    )
    return RoundDetailOut(
        **summary.model_dump(),
        manuscripts=[ManuscriptAdminOut.model_validate(m) for m in round_obj.manuscripts],
        reviewers=[UserOut.model_validate(u) for u in reviewers],
        conflicts=[ConflictOut.model_validate(c) for c in round_obj.conflicts],
        assignments=[AssignmentAdminOut.model_validate(a) for a in round_obj.assignments],
    )


# ---------- manuscripts ----------


@router.post(
    "/rounds/{round_id}/manuscripts",
    response_model=ManuscriptAdminOut,
    status_code=status.HTTP_201_CREATED,
)
def add_manuscript(round_id: int, body: ManuscriptIn, db: Session = Depends(get_db)):
    round_obj = _get_round_locked(db, round_id, require_open=True)
    count = db.scalar(
        select(func.count(Manuscript.id)).where(Manuscript.round_id == round_obj.id)
    )
    if (count or 0) >= settings.max_manuscripts_per_round:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"a round contains at most {settings.max_manuscripts_per_round} manuscripts",
        )
    next_no = (
        db.scalar(
            select(func.coalesce(func.max(Manuscript.manuscript_no), 0)).where(
                Manuscript.round_id == round_obj.id
            )
        )
        or 0
    ) + 1
    manuscript = Manuscript(
        round_id=round_obj.id,
        manuscript_no=next_no,
        title=body.title,
        abstract=body.abstract,
        author_name=body.author_name,
        revision_no=body.revision_no,
    )
    db.add(manuscript)
    db.commit()
    db.refresh(manuscript)
    return ManuscriptAdminOut.model_validate(manuscript)


class ManuscriptPatch(ManuscriptIn):
    pass


@router.patch("/rounds/{round_id}/manuscripts/{manuscript_no}", response_model=ManuscriptAdminOut)
def update_manuscript(
    round_id: int, manuscript_no: int, body: ManuscriptPatch, db: Session = Depends(get_db)
):
    _get_round_locked(db, round_id, require_open=True)
    manuscript = db.scalar(
        select(Manuscript).where(
            Manuscript.round_id == round_id, Manuscript.manuscript_no == manuscript_no
        )
    )
    if manuscript is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="manuscript not found")
    manuscript.title = body.title
    manuscript.abstract = body.abstract
    manuscript.author_name = body.author_name
    manuscript.revision_no = body.revision_no
    db.commit()
    db.refresh(manuscript)
    return ManuscriptAdminOut.model_validate(manuscript)


@router.post(
    "/rounds/{round_id}/manuscripts/{manuscript_no}/bump-revision",
    response_model=ManuscriptAdminOut,
)
def bump_revision(round_id: int, manuscript_no: int, db: Session = Depends(get_db)):
    """Record that a new revision of the manuscript has arrived."""
    _get_round_locked(db, round_id, require_open=True)
    manuscript = db.scalar(
        select(Manuscript).where(
            Manuscript.round_id == round_id, Manuscript.manuscript_no == manuscript_no
        )
    )
    if manuscript is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="manuscript not found")
    manuscript.revision_no += 1
    db.commit()
    db.refresh(manuscript)
    return ManuscriptAdminOut.model_validate(manuscript)


# ---------- conflicts of interest ----------


@router.post(
    "/rounds/{round_id}/conflicts",
    response_model=ConflictOut,
    status_code=status.HTTP_201_CREATED,
)
def add_conflict(round_id: int, body: ConflictIn, db: Session = Depends(get_db)):
    _get_round_locked(db, round_id, require_open=True)
    _reviewer_or_404(db, body.reviewer_id)
    if body.manuscript_no not in _manuscript_nos(db, round_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="manuscript not found")
    # A conflict cannot coexist with an active assignment.
    existing = db.scalar(
        select(Assignment).where(
            Assignment.round_id == round_id,
            Assignment.reviewer_id == body.reviewer_id,
            Assignment.manuscript_no == body.manuscript_no,
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="reviewer is already assigned this manuscript; remove the assignment first",
        )
    conflict = Conflict(
        round_id=round_id,
        reviewer_id=body.reviewer_id,
        manuscript_no=body.manuscript_no,
        reason=body.reason,
    )
    db.add(conflict)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="conflict already recorded"
        )
    db.refresh(conflict)
    return ConflictOut.model_validate(conflict)


@router.delete("/rounds/{round_id}/conflicts/{conflict_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conflict(round_id: int, conflict_id: int, db: Session = Depends(get_db)):
    _get_round_locked(db, round_id, require_open=True)
    conflict = db.get(Conflict, conflict_id)
    if conflict is None or conflict.round_id != round_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conflict not found")
    db.delete(conflict)
    db.commit()


# ---------- assignments ----------


@router.put("/rounds/{round_id}/assignments", response_model=list[AssignmentAdminOut])
def replace_assignments(round_id: int, body: AssignmentReplaceIn, db: Session = Depends(get_db)):
    """Atomically replace the full assignment matrix for a round."""
    round_obj = _get_round_locked(db, round_id, require_open=True)

    submitted = db.scalar(
        select(func.count(Assignment.id)).where(
            Assignment.round_id == round_obj.id, Assignment.submitted.is_(True)
        )
    )
    if submitted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="assignments cannot change after any review has been final-submitted",
        )

    manuscript_nos = _manuscript_nos(db, round_obj.id)

    # validate every reference before touching rows
    reviewer_ids = {item.reviewer_id for item in body.assignments}
    for rid in reviewer_ids:
        _reviewer_or_404(db, rid)
    for item in body.assignments:
        if item.manuscript_no not in manuscript_nos:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"manuscript {item.manuscript_no} not found",
            )

    conflict_keys = {
        (c.reviewer_id, c.manuscript_no)
        for c in db.scalars(select(Conflict).where(Conflict.round_id == round_obj.id))
    }
    for item in body.assignments:
        if (item.reviewer_id, item.manuscript_no) in conflict_keys:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"assignment of manuscript {item.manuscript_no} to reviewer "
                    f"{item.reviewer_id} violates a conflict of interest"
                ),
            )

    # replace inside the same transaction: reviewers briefly keep zero rows,
    # but no other writer can observe that because we hold the round lock.
    db.execute(Assignment.__table__.delete().where(Assignment.round_id == round_obj.id))
    db.flush()
    created = [
        Assignment(
            round_id=round_obj.id,
            reviewer_id=item.reviewer_id,
            manuscript_no=item.manuscript_no,
        )
        for item in body.assignments
    ]
    db.add_all(created)
    db.commit()
    for a in created:
        db.refresh(a)
    return [AssignmentAdminOut.model_validate(a) for a in created]


@router.delete(
    "/rounds/{round_id}/assignments/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_assignment(round_id: int, assignment_id: int, db: Session = Depends(get_db)):
    _get_round_locked(db, round_id, require_open=True)
    assignment = db.get(Assignment, assignment_id)
    if assignment is None or assignment.round_id != round_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="assignment not found")
    if assignment.submitted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="submitted reviews cannot be removed"
        )
    db.delete(assignment)
    db.commit()


# ---------- freeze ----------


@router.post("/rounds/{round_id}/freeze", response_model=RoundSummaryOut)
def freeze_round(round_id: int, db: Session = Depends(get_db)):
    """Atomically freeze the round once every assignment is final-submitted."""
    round_obj = _get_round_locked(db, round_id)
    if round_obj.is_frozen:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="round already frozen")

    assignments = list(
        db.scalars(select(Assignment).where(Assignment.round_id == round_obj.id))
    )
    if not assignments:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="cannot freeze a round without assignments",
        )
    pending = [a for a in assignments if not a.submitted]
    if pending:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{len(pending)} assignments are not final-submitted yet",
        )

    # defence in depth: conflict/load invariants must still hold at freeze time
    manuscript_nos = _manuscript_nos(db, round_obj.id)
    per_reviewer: dict[int, int] = {}
    for a in assignments:
        per_reviewer[a.reviewer_id] = per_reviewer.get(a.reviewer_id, 0) + 1
        if a.manuscript_no not in manuscript_nos:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="assignment points at missing manuscript"
            )
    if any(n > settings.max_assignments_per_reviewer for n in per_reviewer.values()):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="reviewer load limit violated"
        )
    conflict_rows = list(
        db.scalars(select(Conflict).where(Conflict.round_id == round_obj.id))
    )
    conflict_keys = {(c.reviewer_id, c.manuscript_no) for c in conflict_rows}
    if any((a.reviewer_id, a.manuscript_no) in conflict_keys for a in assignments):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="conflict of interest present at freeze"
        )

    round_obj.is_frozen = True
    round_obj.frozen_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    db.refresh(round_obj)
    return _summary(db, round_obj)
