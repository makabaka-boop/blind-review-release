from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import select

from .config import get_settings
from .database import Base, SessionLocal, engine
from .models import User
from .routers import admin, auth, reviewer
from .security import hash_password

settings = get_settings()

app = FastAPI(title="Peer Review System", version="1.0.0")


@app.on_event("startup")
def on_startup() -> None:
    # CREATE TABLE IF NOT EXISTS is sufficient for this self-contained service.
    Base.metadata.create_all(bind=engine)
    _seed_users()


def _seed_users() -> None:
    db = SessionLocal()
    try:
        if db.get(User, 1) is None:
            db.add(
                User(
                    username=settings.admin_username,
                    display_name="System Administrator",
                    hashed_password=hash_password(settings.admin_password),
                    role="admin",
                )
            )
        # 12 seeded reviewers so a round may freely use any 10 of them.
        for i in range(1, 13):
            username = f"r{i:02d}"
            existing = db.scalar(select(User).where(User.username == username))
            if existing is None:
                db.add(
                    User(
                        username=username,
                        display_name=f"Reviewer {i:02d}",
                        hashed_password=hash_password(settings.reviewer_password),
                        role="reviewer",
                    )
                )
        db.commit()
    finally:
        db.close()


@app.middleware("http")
async def never_cache_api(request, call_next):
    """All API responses - including error pages - carry no-store.

    Review pages expose anonymous numbers pre-freeze and authors post-freeze;
    a shared/browser cache must never serve a stale or cross-identity view.
    """
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):  # pragma: no cover - safety net
    # Never leak stack traces / internals to clients.
    return JSONResponse(status_code=500, content={"detail": "internal server error"})


app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(reviewer.router)
