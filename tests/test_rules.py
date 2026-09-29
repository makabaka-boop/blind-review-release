"""Business invariants enforced by the admin API."""

from .conftest import add_manuscript, set_assignments


def _assignments_resp(client, admin_headers, rid, pairs):
    return set_assignments(client, admin_headers, rid, pairs)


def test_max_twenty_manuscripts(client, admin_headers, round_factory):
    rid = round_factory()
    for i in range(20):
        add_manuscript(client, admin_headers, rid, title=f"M{i}", author=f"A{i}")
    resp = client.post(
        f"/api/admin/rounds/{rid}/manuscripts",
        json={"title": "extra", "author_name": "Z"},
        headers=admin_headers,
    )
    assert resp.status_code == 422
    assert "20" in resp.json()["detail"]


def test_conflict_blocks_assignment(client, admin_headers, round_factory):
    rid = round_factory()
    add_manuscript(client, admin_headers, rid, title="M", author="Alice")
    resp = client.post(
        f"/api/admin/rounds/{rid}/conflicts",
        json={"reviewer_id": 2, "manuscript_no": 1, "reason": "former advisor"},
        headers=admin_headers,
    )
    assert resp.status_code == 201

    resp = _assignments_resp(client, admin_headers, rid, [(2, 1)])
    assert resp.status_code == 409
    body = resp.text.lower()
    assert "conflict" in body

    # other reviewers remain assignable
    assert _assignments_resp(client, admin_headers, rid, [(3, 1)]).status_code == 200


def test_conflict_cannot_be_added_on_existing_assignment(
    client, admin_headers, round_factory
):
    rid = round_factory()
    add_manuscript(client, admin_headers, rid, title="M", author="Alice")
    assert _assignments_resp(client, admin_headers, rid, [(2, 1)]).status_code == 200
    resp = client.post(
        f"/api/admin/rounds/{rid}/conflicts",
        json={"reviewer_id": 2, "manuscript_no": 1},
        headers=admin_headers,
    )
    assert resp.status_code == 409
    # removing the assignment first makes the conflict legal
    assignments = client.get(
        f"/api/admin/rounds/{rid}", headers=admin_headers
    ).json()["assignments"]
    aid = assignments[0]["id"]
    assert client.delete(
        f"/api/admin/rounds/{rid}/assignments/{aid}", headers=admin_headers
    ).status_code == 204
    assert client.post(
        f"/api/admin/rounds/{rid}/conflicts",
        json={"reviewer_id": 2, "manuscript_no": 1},
        headers=admin_headers,
    ).status_code == 201


def test_more_than_three_per_reviewer_rejected(client, admin_headers, round_factory):
    rid = round_factory()
    for i in range(4):
        add_manuscript(client, admin_headers, rid, title=f"M{i}", author=f"A{i}")
    # 3 is fine
    ok = _assignments_resp(client, admin_headers, rid, [(2, 1), (2, 2), (2, 3)])
    assert ok.status_code == 200
    # 4th is rejected (even mixed with other reviewers)
    bad = _assignments_resp(
        client, admin_headers, rid, [(2, 1), (2, 2), (2, 3), (2, 4), (3, 1)]
    )
    assert bad.status_code == 422
    assert "3" in bad.text


def test_more_than_ten_distinct_reviewers_rejected(client, admin_headers, round_factory):
    rid = round_factory()
    add_manuscript(client, admin_headers, rid, title="M", author="A")
    pairs = [(reviewer_id, 1) for reviewer_id in range(2, 12)]  # 10 reviewers
    assert _assignments_resp(client, admin_headers, rid, pairs).status_code == 200
    pairs.append((12, 1))  # 11th
    resp = _assignments_resp(client, admin_headers, rid, pairs)
    assert resp.status_code == 422
    assert "10" in resp.text


def test_assignment_unknown_manuscript_is_404(client, admin_headers, round_factory):
    rid = round_factory()
    resp = _assignments_resp(client, admin_headers, rid, [(2, 7)])
    assert resp.status_code == 404


def test_assignment_unknown_reviewer_is_404(client, admin_headers, round_factory):
    rid = round_factory()
    add_manuscript(client, admin_headers, rid, title="M", author="A")
    resp = _assignments_resp(client, admin_headers, rid, [(999, 1)])
    assert resp.status_code == 404


def test_frozen_round_rejects_admin_mutations(
    client, admin_headers, reviewer_headers_factory, round_factory
):
    r1 = reviewer_headers_factory(1)
    rid = round_factory()
    add_manuscript(client, admin_headers, rid, title="M", author="Alice")
    assignments = _assignments_resp(client, admin_headers, rid, [(2, 1)]).json()
    aid = assignments[0]["id"]
    client.post(
        f"/api/reviewer/assignments/{aid}/submit",
        json={"revision_no": 1, "score": 90, "comment": "ok"},
        headers=r1,
    )
    assert client.post(f"/api/admin/rounds/{rid}/freeze", headers=admin_headers).status_code == 200

    assert client.post(
        f"/api/admin/rounds/{rid}/manuscripts",
        json={"title": "x", "author_name": "y"},
        headers=admin_headers,
    ).status_code == 409
    assert client.patch(
        f"/api/admin/rounds/{rid}/manuscripts/1",
        json={"title": "x", "author_name": "y", "revision_no": 1},
        headers=admin_headers,
    ).status_code == 409
    assert client.post(
        f"/api/admin/rounds/{rid}/conflicts",
        json={"reviewer_id": 3, "manuscript_no": 1},
        headers=admin_headers,
    ).status_code == 409
    assert _assignments_resp(client, admin_headers, rid, [(3, 1)]).status_code == 409
