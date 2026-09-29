import os

import httpx
import pytest

BASE_URL = os.environ.get("API_BASE_URL", "http://api:8000")

ADMIN_USER = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASSWORD", "admin123")
REVIEWER_PASS = os.environ.get("REVIEWER_PASSWORD", "reviewer123")


def login(client: httpx.Client, username: str, password: str) -> str:
    resp = client.post(
        "/api/auth/login", data={"username": username, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.fixture(scope="session", autouse=True)
def _wait_for_api():
    # The compose healthcheck normally gates us, but retry anyway to keep the
    # service usable as a plain `docker compose run` target.
    last = None
    for _ in range(60):
        try:
            resp = httpx.get(f"{BASE_URL}/health", timeout=2)
            if resp.status_code == 200:
                return
        except httpx.TransportError as exc:  # pragma: no cover
            last = exc
    raise RuntimeError(f"API never became healthy: {last}")


@pytest.fixture()
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10) as c:
        yield c


@pytest.fixture()
def admin_headers(client):
    token = login(client, ADMIN_USER, ADMIN_PASS)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def reviewer_headers_factory(client):
    def _make(index: int) -> dict[str, str]:
        token = login(client, f"r{index:02d}", REVIEWER_PASS)
        return {"Authorization": f"Bearer {token}"}

    return _make


@pytest.fixture()
def round_factory(client, admin_headers):
    """Create a fresh empty round per test and return (round_id, helpers)."""
    counter = {"n": 0}

    def _create(name: str | None = None):
        counter["n"] += 1
        round_name = name or f"verify-{os.getpid()}-{counter['n']}"
        resp = client.post(
            "/api/admin/rounds", json={"name": round_name}, headers=admin_headers
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["id"]

    return _create


def add_manuscript(client, headers, round_id, *, title, author, abstract="abstract", revision=1):
    resp = client.post(
        f"/api/admin/rounds/{round_id}/manuscripts",
        json={"title": title, "abstract": abstract, "author_name": author, "revision_no": revision},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def set_assignments(client, headers, round_id, pairs):
    resp = client.put(
        f"/api/admin/rounds/{round_id}/assignments",
        json={"assignments": [{"reviewer_id": r, "manuscript_no": m} for r, m in pairs]},
        headers=headers,
    )
    return resp
