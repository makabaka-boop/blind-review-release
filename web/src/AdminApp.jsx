import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "./api.js";

function errText(err) {
  if (err instanceof ApiError) {
    if (typeof err.detail === "string") return err.detail;
    if (err.detail?.error === "draft_version_conflict") {
      return `版本冲突，当前版本为 ${err.detail.current_version}`;
    }
    return JSON.stringify(err.detail);
  }
  return String(err);
}

export default function AdminApp() {
  const [rounds, setRounds] = useState([]);
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);
  const [reviewers, setReviewers] = useState([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const refresh = useCallback(async () => {
    setError("");
    try {
      const [rs, revList] = await Promise.all([
        api("/admin/rounds"),
        api("/admin/reviewers"),
      ]);
      setRounds(rs);
      setReviewers(revList);
      if (selected) {
        const d = await api(`/admin/rounds/${selected}`);
        setDetail(d);
      }
    } catch (e) {
      setError(errText(e));
    }
  }, [selected]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function createRound(e) {
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      const r = await api("/admin/rounds", {
        method: "POST",
        body: { name: fd.get("name") },
      });
      setSelected(r.id);
      e.target.reset();
      setNotice(`已创建轮次 #${r.id}`);
    } catch (e) {
      setError(errText(e));
    }
  }

  return (
    <div className="grid2">
      <div>
        <div className="panel">
          <h2>评审轮次</h2>
          {error && <div className="error">{error}</div>}
          {notice && <div className="ok-msg">{notice}</div>}
          <form className="row" onSubmit={createRound}>
            <input name="name" placeholder="新一轮名称" required />
            <button>新建轮次</button>
          </form>
          <table style={{ marginTop: 10 }}>
            <thead>
              <tr>
                <th>#</th>
                <th>名称</th>
                <th>状态</th>
                <th>进度</th>
              </tr>
            </thead>
            <tbody>
              {rounds.map((r) => (
                <tr
                  key={r.id}
                  className="clickable"
                  onClick={() => setSelected(r.id)}
                  style={{
                    background: selected === r.id ? "#ebf4ff" : undefined,
                  }}
                >
                  <td>{r.id}</td>
                  <td>{r.name}</td>
                  <td>
                    {r.is_frozen ? (
                      <span className="badge frozen">已冻结</span>
                    ) : (
                      <span className="badge open">进行中</span>
                    )}
                  </td>
                  <td>
                    {r.submitted_count}/{r.assignment_count} 已提交 ·{" "}
                    {r.manuscript_count} 稿 · {r.reviewer_count} 评审
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div>
        {detail ? (
          <RoundDetail
            key={detail.id}
            detail={detail}
            reviewers={reviewers}
            onChange={refresh}
            onError={setError}
            onNotice={setNotice}
          />
        ) : (
          <div className="panel muted">请选择或创建一个轮次。</div>
        )}
      </div>
    </div>
  );
}

function RoundDetail({ detail, reviewers, onChange, onError, onNotice }) {
  const frozen = detail.is_frozen;

  return (
    <>
      <div className="panel">
        <h2>
          轮次 #{detail.id} · {detail.name}{" "}
          {frozen ? (
            <span className="badge frozen">已冻结 · 作者已揭示</span>
          ) : (
            <span className="badge open">进行中</span>
          )}
        </h2>
        <div className="muted">
          稿件 {detail.manuscript_count}/20 · 评审员 {detail.reviewer_count}/10 ·
          最终提交 {detail.submitted_count}/{detail.assignment_count}
        </div>
        {!frozen && (
          <FreezeButton detail={detail} onChange={onChange} onError={onError} />
        )}
      </div>

      <ManuscriptsPanel detail={detail} frozen={frozen} onChange={onChange} onError={onError} />
      <ConflictsPanel detail={detail} reviewers={reviewers} frozen={frozen} onChange={onChange} onError={onError} />
      <AssignmentsPanel
        detail={detail}
        reviewers={reviewers}
        frozen={frozen}
        onChange={onChange}
        onError={onError}
        onNotice={onNotice}
      />
    </>
  );
}

function FreezeButton({ detail, onChange, onError }) {
  const [busy, setBusy] = useState(false);
  const ready = detail.assignment_count > 0 && detail.submitted_count === detail.assignment_count;

  async function freeze() {
    if (!window.confirm("冻结后作者身份将对评审员揭示，且所有内容不可修改。确认冻结？")) return;
    setBusy(true);
    try {
      await api(`/admin/rounds/${detail.id}/freeze`, { method: "POST" });
      await onChange();
    } catch (e) {
      onError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ marginTop: 10 }}>
      <button disabled={!ready || busy} onClick={freeze}>
        {ready ? "原子冻结整轮并揭示作者" : "尚有评审未最终提交，无法冻结"}
      </button>
    </div>
  );
}

function ManuscriptsPanel({ detail, frozen, onChange, onError }) {
  const [form, setForm] = useState({
    title: "",
    author_name: "",
    abstract: "",
    revision_no: 1,
  });

  async function add(e) {
    e.preventDefault();
    try {
      await api(`/admin/rounds/${detail.id}/manuscripts`, {
        method: "POST",
        body: form,
      });
      setForm({ title: "", author_name: "", abstract: "", revision_no: 1 });
      await onChange();
    } catch (e) {
      onError(errText(e));
    }
  }

  async function bump(no) {
    try {
      await api(`/admin/rounds/${detail.id}/manuscripts/${no}/bump-revision`, {
        method: "POST",
      });
      await onChange();
    } catch (e) {
      onError(errText(e));
    }
  }

  return (
    <div className="panel">
      <h2>稿件（最多 20 份）</h2>
      <table>
        <thead>
          <tr>
            <th>匿名稿号</th>
            <th>标题</th>
            <th>作者{frozen ? "（已揭示）" : "（对评审员隐藏）"}</th>
            <th>修订号</th>
            {!frozen && <th></th>}
          </tr>
        </thead>
        <tbody>
          {detail.manuscripts.map((m) => (
            <tr key={m.id}>
              <td>#{m.manuscript_no}</td>
              <td>{m.title}</td>
              <td>{m.author_name}</td>
              <td>r{m.revision_no}</td>
              {!frozen && (
                <td>
                  <button className="small ghost" onClick={() => bump(m.manuscript_no)}>
                    新修订到达 +1
                  </button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      {!frozen && detail.manuscripts.length < 20 && (
        <form onSubmit={add} style={{ marginTop: 10 }}>
          <div className="grid2">
            <div>
              <label>标题</label>
              <input
                value={form.title}
                onChange={(e) => setForm({ ...form, title: e.target.value })}
                required
              />
            </div>
            <div>
              <label>作者姓名（冻结前对评审员不可见）</label>
              <input
                value={form.author_name}
                onChange={(e) => setForm({ ...form, author_name: e.target.value })}
                required
              />
            </div>
          </div>
          <label>摘要</label>
          <textarea
            value={form.abstract}
            onChange={(e) => setForm({ ...form, abstract: e.target.value })}
          />
          <div style={{ marginTop: 8 }}>
            <button>添加稿件</button>
          </div>
        </form>
      )}
    </div>
  );
}

function ConflictsPanel({ detail, reviewers, frozen, onChange, onError }) {
  const [rid, setRid] = useState(reviewers[0]?.id ?? "");
  const [no, setNo] = useState(1);
  const [reason, setReason] = useState("");

  async function add(e) {
    e.preventDefault();
    try {
      await api(`/admin/rounds/${detail.id}/conflicts`, {
        method: "POST",
        body: { reviewer_id: Number(rid), manuscript_no: Number(no), reason },
      });
      setReason("");
      await onChange();
    } catch (e) {
      onError(errText(e));
    }
  }

  async function remove(cid) {
    try {
      await api(`/admin/rounds/${detail.id}/conflicts/${cid}`, { method: "DELETE" });
      await onChange();
    } catch (e) {
      onError(errText(e));
    }
  }

  const nameOf = (id) => reviewers.find((r) => r.id === id)?.display_name ?? `#${id}`;

  return (
    <div className="panel">
      <h2>利益冲突</h2>
      {detail.conflicts.length === 0 && <div className="muted">暂无冲突记录。</div>}
      {detail.conflicts.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>评审员</th>
              <th>稿号</th>
              <th>原因</th>
              {!frozen && <th></th>}
            </tr>
          </thead>
          <tbody>
            {detail.conflicts.map((c) => (
              <tr key={c.id}>
                <td>{nameOf(c.reviewer_id)}</td>
                <td>#{c.manuscript_no}</td>
                <td>{c.reason || "—"}</td>
                {!frozen && (
                  <td>
                    <button className="small danger" onClick={() => remove(c.id)}>
                      删除
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {!frozen && (
        <form className="row" onSubmit={add} style={{ marginTop: 10 }}>
          <select value={rid} onChange={(e) => setRid(e.target.value)}>
            {reviewers.map((r) => (
              <option key={r.id} value={r.id}>
                {r.display_name}
              </option>
            ))}
          </select>
          <input
            type="number"
            min={1}
            value={no}
            style={{ width: 90 }}
            onChange={(e) => setNo(e.target.value)}
          />
          <input
            placeholder="冲突原因（可选）"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          <button>登记冲突</button>
        </form>
      )}
    </div>
  );
}

function AssignmentsPanel({ detail, reviewers, frozen, onChange, onError, onNotice }) {
  // Matrix: reviewer_id -> set(manuscript_no)
  const initial = useMemo(() => {
    const m = {};
    for (const a of detail.assignments) {
      (m[a.reviewer_id] ??= new Set()).add(a.manuscript_no);
    }
    return m;
  }, [detail]);
  const [matrix, setMatrix] = useState(initial);

  useEffect(() => setMatrix(initial), [initial]);

  const involvedReviewers = Object.keys(matrix).map(Number);
  const perReviewer = Object.fromEntries(
    involvedReviewers.map((id) => [id, matrix[id].size])
  );
  const loadViolation = involvedReviewers.some((id) => perReviewer[id] > 3);
  const reviewerViolation = involvedReviewers.length > 10;
  const submittedAny = detail.assignments.some((a) => a.submitted);
  const conflictSet = new Set(
    detail.conflicts.map((c) => `${c.reviewer_id}:${c.manuscript_no}`)
  );
  const conflictViolations = involvedReviewers.flatMap((rid) =>
    [...matrix[rid]]
      .filter((no) => conflictSet.has(`${rid}:${no}`))
      .map((no) => ({ rid, no }))
  );

  function toggle(rid, no) {
    setMatrix((prev) => {
      const next = Object.fromEntries(
        Object.entries(prev).map(([k, v]) => [k, new Set(v)])
      );
      (next[rid] ??= new Set());
      if (next[rid].has(no)) next[rid].delete(no);
      else next[rid].add(no);
      if (next[rid].size === 0) delete next[rid];
      return next;
    });
  }

  async function save() {
    const payload = [];
    for (const [ridStr, nos] of Object.entries(matrix)) {
      for (const no of nos) payload.push({ reviewer_id: Number(ridStr), manuscript_no: no });
    }
    try {
      await api(`/admin/rounds/${detail.id}/assignments`, {
        method: "PUT",
        body: { assignments: payload },
      });
      onNotice("分配已保存");
      await onChange();
    } catch (e) {
      onError(errText(e));
    }
  }

  const submittedIds = new Set(
    detail.assignments.filter((a) => a.submitted).map((a) => a.id)
  );

  return (
    <div className="panel">
      <h2>手动分配（每人 ≤ 3，评审员 ≤ 10）</h2>
      {submittedAny && !frozen && (
        <div className="error">
          已有评审最终提交，分配矩阵已锁定；如需调整须由评审员在冻结前的轮次中处理。
        </div>
      )}
      {conflictViolations.length > 0 && (
        <div className="error">
          存在利益冲突：
          {conflictViolations
            .map((v) => `#${reviewers.find((r) => r.id === v.rid)?.display_name ?? v.rid}→稿${v.no}`)
            .join("，")}
          ，不可保存。
        </div>
      )}
      {loadViolation && <div className="error">存在评审员被分配超过 3 份，不可保存。</div>}
      {reviewerViolation && <div className="error">参与评审员超过 10 人，不可保存。</div>}

      <table>
        <thead>
          <tr>
            <th>评审员</th>
            {detail.manuscripts.map((m) => (
              <th key={m.manuscript_no} title={m.title}>
                #{m.manuscript_no}
                <div className="muted" style={{ fontWeight: 400 }}>r{m.revision_no}</div>
              </th>
            ))}
            <th>数量</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          {reviewers.map((r) => {
            const set = matrix[r.id] ?? new Set();
            const submittedHere = detail.assignments.filter(
              (a) => a.reviewer_id === r.id && a.submitted
            ).length;
            return (
              <tr key={r.id}>
                <td>
                  {r.display_name}
                  {detail.conflicts.some((c) => c.reviewer_id === r.id) && (
                    <span className="pill" title="该评审员在本轮有利益冲突">
                      有冲突
                    </span>
                  )}
                </td>
                {detail.manuscripts.map((m) => {
                  const checked = set.has(m.manuscript_no);
                  const isConflict = conflictSet.has(`${r.id}:${m.manuscript_no}`);
                  return (
                    <td key={m.manuscript_no} style={{ textAlign: "center" }}>
                      <input
                        type="checkbox"
                        checked={checked}
                        disabled={frozen || submittedAny}
                        onChange={() => toggle(r.id, m.manuscript_no)}
                        title={isConflict ? "利益冲突，禁止勾选" : ""}
                        style={isConflict ? { accentColor: "red" } : undefined}
                      />
                      {isConflict && <span title="利益冲突">⚠</span>}
                    </td>
                  );
                })}
                <td style={{ color: set.size > 3 ? "var(--danger)" : undefined }}>
                  {set.size}
                </td>
                <td>
                  {submittedHere > 0 && (
                    <span className="badge done">{submittedHere} 已提交</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {!frozen && !submittedAny && (
        <div style={{ marginTop: 10 }}>
          <button
            disabled={loadViolation || reviewerViolation || conflictViolations.length > 0}
            onClick={save}
          >
            保存全部分配（原子替换）
          </button>
        </div>
      )}
    </div>
  );
}
