"""Anonymity: author identity must not leak through lists, details, errors or cache."""

import json

from .conftest import add_manuscript, set_assignments

SENSITIVE_MARKERS = ["alice", "bob", "confidential-author"]


def _assert_clean(text: str):
    lowered = text.lower()
    for marker in SENSITIVE_MARKERS:
        assert marker not in lowered, f"sensitive value leaked: {marker}"


def test_reviewer_views_are_anonymous_before_freeze(
    client, admin_headers, reviewer_headers_factory, round_factory
):
    r1 = reviewer_headers_factory(1)
    rid = round_factory()
    add_manuscript(client, admin_headers, rid, title="Public Title 1", author="Alice Confidential-Author")
    add_manuscript(client, admin_headers, rid, title="Public Title 2", author="Bob Confidential-Author")
    assignments = set_assignments(client, admin_headers, rid, [(2, 1)]).json()
    aid = assignments[0]["id"]

    # round list endpoint
    resp = client.get(f"/api/reviewer/rounds/{rid}", headers=r1)
    assert resp.status_code == 200
    _assert_clean(resp.text)
    data = resp.json()
    # non-assigned manuscript exposes only its number
    assert {"manuscript_no": 2, "revision_no": 1} in data["manuscripts"]
    for m in data["manuscripts"]:
        assert "author_name" not in m
        assert "author" not in m
    # assigned one is also anonymous pre-freeze: the key must be absent,
    # not merely null
    assert "author_name" not in data["assignments"][0]

    # own assignment list
    listing = client.get("/api/reviewer/rounds", headers=r1)
    _assert_clean(listing.text)
    assert listing.status_code == 200

    # detail endpoint
    detail = client.get(f"/api/reviewer/assignments/{aid}", headers=r1)
    assert detail.status_code == 200
    _assert_clean(detail.text)
    assert "author_name" not in detail.json()


def test_error_responses_never_mention_authors(
    client, admin_headers, reviewer_headers_factory, round_factory
):
    r1 = reviewer_headers_factory(1)
    rid = round_factory()
    add_manuscript(client, admin_headers, rid, title="T", author="Alice Confidential-Author")

    for path in [
        f"/api/reviewer/rounds/{rid}",
        "/api/reviewer/rounds/999999",
        "/api/reviewer/assignments/999999",
    ]:
        resp = client.get(path, headers=r1)
        assert resp.status_code in (403, 404)
        _assert_clean(resp.text)

    # malformed/forbidden writes against guessed ids
    resp = client.put(
        "/api/reviewer/assignments/999999/draft",
        json={"revision_no": 1, "score": 1, "comment": "x"},
        headers=r1,
    )
    assert resp.status_code == 404
    _assert_clean(resp.text)


def test_api_responses_are_unconditionally_no_store(
    client, admin_headers, reviewer_headers_factory, round_factory
):
    r1 = reviewer_headers_factory(1)
    rid = round_factory()
    add_manuscript(client, admin_headers, rid, title="T", author="Alice")
    set_assignments(client, admin_headers, rid, [(2, 1)])

    for resp in [
        client.get(f"/api/reviewer/rounds/{rid}", headers=r1),
        client.get("/api/reviewer/rounds", headers=r1),
        client.get("/api/admin/rounds", headers=admin_headers),
        client.get("/api/reviewer/assignments/999999", headers=r1),
        client.post("/api/auth/login", data={"username": "ghost", "password": "x"}),
    ]:
        cc = resp.headers.get("cache-control", "")
        assert "no-store" in cc, f"missing no-store: {resp.request.url} -> {cc}"


def test_authors_revealed_only_after_freeze(
    client, admin_headers, reviewer_headers_factory, round_factory
):
    r1 = reviewer_headers_factory(1)
    rid = round_factory()
    add_manuscript(client, admin_headers, rid, title="T1", author="Alice Confidential-Author")
    aid = set_assignments(client, admin_headers, rid, [(2, 1)]).json()[0]["id"]

    client.post(
        f"/api/reviewer/assignments/{aid}/submit",
        json={"revision_no": 1, "score": 90, "comment": "ok"},
        headers=r1,
    )
    # pre-freeze sanity
    _assert_clean(client.get(f"/api/reviewer/rounds/{rid}", headers=r1).text)

    assert client.post(f"/api/admin/rounds/{rid}/freeze", headers=admin_headers).status_code == 200

    revealed = client.get(f"/api/reviewer/rounds/{rid}", headers=r1).json()
    assert revealed["is_frozen"] is True
    assert revealed["manuscripts"][0]["author_name"] == "Alice Confidential-Author"
    assert revealed["assignments"][0]["author_name"] == "Alice Confidential-Author"

    detail = client.get(f"/api/reviewer/assignments/{aid}", headers=r1).json()
    assert detail["author_name"] == "Alice Confidential-Author"


def test_no_author_fields_anywhere_in_raw_json_pre_freeze(
    client, admin_headers, reviewer_headers_factory, round_factory
):
    """Recursively walk every reviewer-accessible JSON blob and fail on author keys."""
    # Use a reviewer untouched by other tests so the rounds list is unpolluted
    # by rounds that were legitimately frozen/revealed elsewhere.
    fresh = reviewer_headers_factory(7)
    rid = round_factory()
    add_manuscript(client, admin_headers, rid, title="T1", author="Alice Secret-Name")
    aid = set_assignments(client, admin_headers, rid, [(8, 1)]).json()[0]["id"]

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                assert "author" not in key.lower(), f"unexpected key {key}"
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, str):
            assert "alice" not in node.lower() and "secret-name" not in node.lower()

    for resp in [
        client.get("/api/reviewer/rounds", headers=fresh),
        client.get(f"/api/reviewer/rounds/{rid}", headers=fresh),
        client.get(f"/api/reviewer/assignments/{aid}", headers=fresh),
    ]:
        walk(resp.json())

    # and the raw payload should not contain the author string either
    raw = client.get(f"/api/reviewer/rounds/{rid}", headers=fresh).text
    json.loads(raw)  # parses
    assert "Alice" not in raw and "Secret-Name" not in raw
