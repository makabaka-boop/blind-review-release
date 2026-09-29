"""Drafts per revision, final submission and freeze gating."""

from .conftest import add_manuscript, set_assignments


def _setup_round_with_assignment(client, admin_headers, reviewer_headers_factory, round_factory):
    rid = round_factory()
    add_manuscript(client, admin_headers, rid, title="Secret", author="Alice")
    r1 = reviewer_headers_factory(1)
    aid = set_assignments(client, admin_headers, rid, [(2, 1)]).json()[0]["id"]
    return rid, aid, r1


def test_draft_lifecycle_and_optimistic_version(
    client, admin_headers, reviewer_headers_factory, round_factory
):
    rid, aid, r1 = _setup_round_with_assignment(
        client, admin_headers, reviewer_headers_factory, round_factory
    )
    resp = client.put(
        f"/api/reviewer/assignments/{aid}/draft",
        json={"revision_no": 1, "score": 70, "comment": "v1 draft"},
        headers=r1,
    )
    assert resp.status_code == 200
    assert resp.json()["version"] == 1

    resp = client.put(
        f"/api/reviewer/assignments/{aid}/draft",
        json={"revision_no": 1, "score": 75, "comment": "v1 draft 2", "expected_version": 1},
        headers=r1,
    )
    assert resp.status_code == 200
    assert resp.json()["version"] == 2

    # stale writer loses the race
    stale = client.put(
        f"/api/reviewer/assignments/{aid}/draft",
        json={"revision_no": 1, "score": 10, "comment": "stale", "expected_version": 1},
        headers=r1,
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["current_version"] == 2

    # content of the winning write is intact
    detail = client.get(f"/api/reviewer/assignments/{aid}", headers=r1).json()
    assert detail["drafts"][0]["score"] == 75
    assert detail["drafts"][0]["version"] == 2


def test_draft_follows_revision_number(client, admin_headers, reviewer_headers_factory, round_factory):
    rid, aid, r1 = _setup_round_with_assignment(
        client, admin_headers, reviewer_headers_factory, round_factory
    )
    assert client.put(
        f"/api/reviewer/assignments/{aid}/draft",
        json={"revision_no": 1, "score": 70, "comment": "rev1"},
        headers=r1,
    ).status_code == 200

    # admin records a new revision; the reviewer may KEEP a draft on the old
    # revision number (history preserved) but final submit must target the
    # current revision.
    assert client.post(
        f"/api/admin/rounds/{rid}/manuscripts/1/bump-revision", headers=admin_headers
    ).status_code == 200
    old = client.put(
        f"/api/reviewer/assignments/{aid}/draft",
        json={"revision_no": 1, "score": 71, "comment": "still rev1"},
        headers=r1,
    )
    assert old.status_code == 200

    new = client.put(
        f"/api/reviewer/assignments/{aid}/draft",
        json={"revision_no": 2, "score": 80, "comment": "rev2"},
        headers=r1,
    )
    assert new.status_code == 200

    # final submit against a stale revision is rejected
    stale_submit = client.post(
        f"/api/reviewer/assignments/{aid}/submit",
        json={"revision_no": 1, "score": 71, "comment": "old"},
        headers=r1,
    )
    assert stale_submit.status_code == 409

    # both revisions' drafts are preserved independently
    detail = client.get(f"/api/reviewer/assignments/{aid}", headers=r1).json()
    revisions = {d["revision_no"]: d for d in detail["drafts"]}
    assert set(revisions) == {1, 2}
    assert revisions[1]["comment"] == "still rev1"
    assert revisions[2]["comment"] == "rev2"


def test_final_submit_requires_score(client, admin_headers, reviewer_headers_factory, round_factory):
    rid, aid, r1 = _setup_round_with_assignment(
        client, admin_headers, reviewer_headers_factory, round_factory
    )
    resp = client.post(
        f"/api/reviewer/assignments/{aid}/submit",
        json={"revision_no": 1, "score": None, "comment": ""},
        headers=r1,
    )
    assert resp.status_code == 422


def test_submit_then_seal_and_freeze(client, admin_headers, reviewer_headers_factory, round_factory):
    rid, aid, r1 = _setup_round_with_assignment(
        client, admin_headers, reviewer_headers_factory, round_factory
    )
    # cannot freeze with zero submitted
    assert client.post(f"/api/admin/rounds/{rid}/freeze", headers=admin_headers).status_code == 409

    assert client.post(
        f"/api/reviewer/assignments/{aid}/submit",
        json={"revision_no": 1, "score": 88, "comment": "accept"},
        headers=r1,
    ).status_code == 200

    # frozen content: no more drafts, no second submit
    assert client.put(
        f"/api/reviewer/assignments/{aid}/draft",
        json={"revision_no": 1, "score": 1, "comment": "tamper"},
        headers=r1,
    ).status_code == 409
    assert client.post(
        f"/api/reviewer/assignments/{aid}/submit",
        json={"revision_no": 1, "score": 1, "comment": "tamper"},
        headers=r1,
    ).status_code == 409

    # admin cannot reshuffle assignments after a submit
    assert set_assignments(client, admin_headers, rid, []).status_code == 409

    frozen = client.post(f"/api/admin/rounds/{rid}/freeze", headers=admin_headers)
    assert frozen.status_code == 200
    assert frozen.json()["is_frozen"] is True
    assert frozen.json()["submitted_count"] == 1

    # idempotency is NOT allowed: second freeze reports conflict, content unchanged
    again = client.post(f"/api/admin/rounds/{rid}/freeze", headers=admin_headers)
    assert again.status_code == 409

    # the final content survives the freeze unchanged
    detail = client.get(f"/api/reviewer/assignments/{aid}", headers=r1).json()
    assert detail["submitted"] is True
    assert detail["drafts"][-1]["score"] == 88
    assert detail["drafts"][-1]["comment"] == "accept"


def test_freeze_blocked_until_every_assignment_submitted(
    client, admin_headers, reviewer_headers_factory, round_factory
):
    rid = round_factory()
    add_manuscript(client, admin_headers, rid, title="M1", author="A")
    add_manuscript(client, admin_headers, rid, title="M2", author="B")
    r1 = reviewer_headers_factory(1)
    r2 = reviewer_headers_factory(2)
    assignments = set_assignments(
        client, admin_headers, rid, [(2, 1), (3, 2)]
    ).json()
    a1 = next(a["id"] for a in assignments if a["reviewer_id"] == 2)

    assert client.post(
        f"/api/reviewer/assignments/{a1}/submit",
        json={"revision_no": 1, "score": 90, "comment": "ok"},
        headers=r1,
    ).status_code == 200
    blocked = client.post(f"/api/admin/rounds/{rid}/freeze", headers=admin_headers)
    assert blocked.status_code == 409

    a2 = next(a["id"] for a in assignments if a["reviewer_id"] == 3)
    assert client.post(
        f"/api/reviewer/assignments/{a2}/submit",
        json={"revision_no": 1, "score": 60, "comment": "ok"},
        headers=r2,
    ).status_code == 200
    assert client.post(f"/api/admin/rounds/{rid}/freeze", headers=admin_headers).status_code == 200
