"""Concurrency: submit/freeze interleave and optimistic draft conflicts.

These tests only assert externally observable invariants, never timing:
regardless of how the operations interleave, the resulting state must be one
of the legal states and content must be internally consistent.
"""

import threading

import httpx

from .conftest import BASE_URL, add_manuscript, set_assignments


def test_concurrent_final_submit_and_freeze(reviewer_headers_factory, admin_headers, round_factory):
    client = httpx.Client(base_url=BASE_URL, timeout=30)
    rid = round_factory()
    add_manuscript(client, admin_headers, rid, title="M1", author="Alice")
    add_manuscript(client, admin_headers, rid, title="M2", author="Bob")
    r1 = reviewer_headers_factory(1)
    r2 = reviewer_headers_factory(2)
    assignments = set_assignments(client, admin_headers, rid, [(2, 1), (3, 2)]).json()
    a1 = next(a["id"] for a in assignments if a["reviewer_id"] == 2)
    a2 = next(a["id"] for a in assignments if a["reviewer_id"] == 3)

    # submit assignment 1 up front; then race the LAST submit against freeze
    first = client.post(
        f"/api/reviewer/assignments/{a1}/submit",
        json={"revision_no": 1, "score": 70, "comment": "first"},
        headers=r1,
    )
    assert first.status_code == 200

    barrier = threading.Barrier(2)
    outcomes: dict[str, int] = {}

    def do_submit():
        barrier.wait()
        resp = client.post(
            f"/api/reviewer/assignments/{a2}/submit",
            json={"revision_no": 1, "score": 70, "comment": "racer"},
            headers=r2,
        )
        outcomes["submit"] = resp.status_code

    def do_freeze():
        barrier.wait()
        resp = client.post(f"/api/admin/rounds/{rid}/freeze", headers=admin_headers)
        outcomes["freeze"] = resp.status_code

    t1 = threading.Thread(target=do_submit)
    t2 = threading.Thread(target=do_freeze)
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    round_state = client.get(f"/api/admin/rounds/{rid}", headers=admin_headers).json()

    if round_state["is_frozen"]:
        # Legal order: the final submit committed first, freeze then succeeded.
        assert outcomes["submit"] == 200
        assert outcomes["freeze"] == 200
        assert round_state["submitted_count"] == 2
    else:
        # Legal order: freeze was evaluated first; it must have been rejected
        # and nothing must be half-applied.
        assert outcomes["freeze"] == 409
        assert outcomes["submit"] == 200
        assert round_state["submitted_count"] == 2
        # now the sequential freeze must succeed and remain consistent
        again = client.post(f"/api/admin/rounds/{rid}/freeze", headers=admin_headers)
        assert again.status_code == 200
        round_state = client.get(f"/api/admin/rounds/{rid}", headers=admin_headers).json()
        assert round_state["is_frozen"] is True
        assert round_state["submitted_count"] == 2

    # frozen content is immutable afterwards for every role
    assert client.put(
        f"/api/reviewer/assignments/{a2}/draft",
        json={"revision_no": 1, "score": 1, "comment": "tamper"},
        headers=r2,
    ).status_code in (409, 422)
    assert client.post(
        f"/api/reviewer/assignments/{a2}/submit",
        json={"revision_no": 1, "score": 1, "comment": "tamper"},
        headers=r2,
    ).status_code == 409

    detail = client.get(f"/api/reviewer/assignments/{a2}", headers=r2).json()
    assert detail["submitted"] is True
    assert detail["drafts"][-1]["comment"] == "racer"
    client.close()


def test_parallel_draft_writers_only_one_version_wins(
    reviewer_headers_factory, admin_headers, round_factory
):
    client = httpx.Client(base_url=BASE_URL, timeout=30)
    rid = round_factory()
    add_manuscript(client, admin_headers, rid, title="M1", author="Alice")
    r1 = reviewer_headers_factory(1)
    aid = set_assignments(client, admin_headers, rid, [(2, 1)]).json()[0]["id"]

    # seed version 1 without optimistic token
    assert client.put(
        f"/api/reviewer/assignments/{aid}/draft",
        json={"revision_no": 1, "score": 0, "comment": "seed"},
        headers=r1,
    ).status_code == 200

    results: list[int] = []
    barrier = threading.Barrier(5)
    lock = threading.Lock()

    def write(i):
        # every writer believes it is still at version 1
        token = r1["Authorization"]
        barrier.wait()
        resp = client.put(
            f"/api/reviewer/assignments/{aid}/draft",
            json={"revision_no": 1, "score": i, "comment": f"c{i}", "expected_version": 1},
            headers={"Authorization": token},
        )
        with lock:
            results.append(resp.status_code)

    threads = [threading.Thread(target=write, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert sorted(results).count(200) == 1, results
    assert sorted(results).count(409) == 4, results

    detail = client.get(f"/api/reviewer/assignments/{aid}", headers=r1).json()
    draft = detail["drafts"][0]
    assert draft["version"] == 2  # exactly one update landed
    client.close()


def test_parallel_drafts_on_distinct_revisions_both_survive(
    reviewer_headers_factory, admin_headers, round_factory
):
    client = httpx.Client(base_url=BASE_URL, timeout=30)
    rid = round_factory()
    add_manuscript(client, admin_headers, rid, title="M1", author="Alice", revision=2)
    r1 = reviewer_headers_factory(1)
    aid = set_assignments(client, admin_headers, rid, [(2, 1)]).json()[0]["id"]

    barrier = threading.Barrier(2)
    statuses: list[int] = []
    lock = threading.Lock()

    def write(revision):
        barrier.wait()
        resp = client.put(
            f"/api/reviewer/assignments/{aid}/draft",
            json={"revision_no": revision, "score": revision * 10, "comment": f"r{revision}"},
            headers=r1,
        )
        with lock:
            statuses.append(resp.status_code)

    t1 = threading.Thread(target=write, args=(1,))
    t2 = threading.Thread(target=write, args=(2,))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert statuses == [200, 200]
    detail = client.get(f"/api/reviewer/assignments/{aid}", headers=r1).json()
    revisions = {d["revision_no"]: d for d in detail["drafts"]}
    assert set(revisions) == {1, 2}
    client.close()
