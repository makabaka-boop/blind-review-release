"""Role boundary tests: both roles must be rejected on the other's surface."""

from .conftest import add_manuscript, set_assignments


def test_anonymous_requests_rejected(client):
    assert client.get("/api/admin/rounds").status_code == 401
    assert client.get("/api/reviewer/rounds").status_code == 401
    assert client.get("/api/auth/me").status_code == 401


def test_bogus_token_rejected(client):
    resp = client.get("/api/admin/rounds", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401
    # error body must not echo token material
    assert "not-a-jwt" not in resp.text


def test_reviewer_cannot_reach_admin_surface(client, admin_headers, reviewer_headers_factory):
    r1 = reviewer_headers_factory(1)
    assert client.get("/api/admin/rounds", headers=r1).status_code == 403
    assert client.get("/api/admin/reviewers", headers=r1).status_code == 403

    rid = client.post("/api/admin/rounds", json={"name": "authz"}, headers=admin_headers).json()["id"]
    assert client.post(
        f"/api/admin/rounds/{rid}/freeze", headers=r1
    ).status_code == 403
    assert client.put(
        f"/api/admin/rounds/{rid}/assignments",
        json={"assignments": []},
        headers=r1,
    ).status_code == 403


def test_admin_cannot_reach_reviewer_surface(client, admin_headers):
    assert client.get("/api/reviewer/rounds", headers=admin_headers).status_code == 403


def test_login_wrong_password_uniform_401(client):
    resp = client.post(
        "/api/auth/login", data={"username": "admin", "password": "wrong"}
    )
    assert resp.status_code == 401
    # nonexistent user must look identical to a wrong password
    resp2 = client.post(
        "/api/auth/login", data={"username": "ghost", "password": "x"}
    )
    assert resp2.status_code == 401
    assert resp.json() == resp2.json()


def test_reviewer_cannot_see_other_round_by_id(
    client, admin_headers, reviewer_headers_factory, round_factory
):
    r1 = reviewer_headers_factory(1)
    r3 = reviewer_headers_factory(3)
    rid = round_factory()
    add_manuscript(client, admin_headers, rid, title="T1", author="Alice")
    resp = set_assignments(client, admin_headers, rid, [(2, 1)])
    assert resp.status_code == 200

    # unassigned reviewer gets 404 (not 403): existence must not be disclosed
    assert client.get(f"/api/reviewer/rounds/{rid}", headers=r3).status_code == 404
    # random/guessed round id is the same 404
    assert client.get("/api/reviewer/rounds/999999", headers=r1).status_code == 404


def test_reviewer_cannot_open_other_reviewers_assignment(
    client, admin_headers, reviewer_headers_factory, round_factory
):
    r1 = reviewer_headers_factory(1)  # user id 2, owns assignment A
    r2 = reviewer_headers_factory(2)  # user id 3, owns assignment B
    r3 = reviewer_headers_factory(3)  # user id 4, nothing in this round
    rid = round_factory()
    add_manuscript(client, admin_headers, rid, title="T1", author="Alice")
    assignments = set_assignments(client, admin_headers, rid, [(2, 1), (3, 1)]).json()
    aid_a = next(a["id"] for a in assignments if a["reviewer_id"] == 2)
    aid_b = next(a["id"] for a in assignments if a["reviewer_id"] == 3)

    # assignment B must be reachable by nobody but r2; r1 owns something else
    # in the same round and r3 is a complete outsider, but both get 404 alike
    for headers in (r1, r3):
        assert client.get(
            f"/api/reviewer/assignments/{aid_b}", headers=headers
        ).status_code == 404
        assert client.put(
            f"/api/reviewer/assignments/{aid_b}/draft",
            json={"revision_no": 1, "score": 1, "comment": "x"},
            headers=headers,
        ).status_code == 404
        assert client.post(
            f"/api/reviewer/assignments/{aid_b}/submit",
            json={"revision_no": 1, "score": 1, "comment": "x"},
            headers=headers,
        ).status_code == 404

    # each owner reaches only their own
    assert client.get(f"/api/reviewer/assignments/{aid_a}", headers=r1).status_code == 200
    assert client.get(f"/api/reviewer/assignments/{aid_b}", headers=r2).status_code == 200
