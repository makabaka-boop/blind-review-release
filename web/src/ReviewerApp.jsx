import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "./api.js";

function errText(err) {
  if (err instanceof ApiError) {
    if (typeof err.detail === "string") return err.detail;
    if (err.detail?.error === "draft_version_conflict") {
      return `草稿版本冲突：他人已更新（当前版本 ${err.detail.current_version}），请刷新后重试`;
    }
    return JSON.stringify(err.detail);
  }
  return String(err);
}

export default function ReviewerApp() {
  const [rounds, setRounds] = useState([]);
  const [selected, setSelected] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const rs = await api("/reviewer/rounds");
      setRounds(rs);
      if (selected && !rs.some((r) => r.id === selected)) setSelected(null);
    } catch (e) {
      setError(errText(e));
    }
  }, [selected]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="grid2">
      <div className="panel">
        <h2>我的评审轮次</h2>
        {error && <div className="error">{error}</div>}
        {rounds.length === 0 && <div className="muted">当前没有分配给你的轮次。</div>}
        {rounds.map((r) => (
          <div
            key={r.id}
            className="clickable panel"
            style={{
              padding: 10,
              marginBottom: 8,
              background: selected === r.id ? "#ebf4ff" : undefined,
            }}
            onClick={() => setSelected(r.id)}
          >
            <strong>#{r.id} {r.name}</strong>{" "}
            {r.is_frozen ? (
              <span className="badge frozen">已冻结 · 作者已揭示</span>
            ) : (
              <span className="badge open">匿名评审中</span>
            )}
            <div className="muted" style={{ marginTop: 4 }}>
              本轮共 {r.manuscripts.length} 份稿件 · 分配给我 {r.assignments.length} 份
            </div>
          </div>
        ))}
      </div>
      <div>
        {selected ? (
          <RoundView key={selected} roundId={selected} onError={setError} />
        ) : (
          <div className="panel muted">请选择一个轮次查看你的分配。</div>
        )}
      </div>
    </div>
  );
}

function RoundView({ roundId, onError }) {
  const [round, setRound] = useState(null);
  const [openAssignment, setOpenAssignment] = useState(null);

  const load = useCallback(async () => {
    try {
      setRound(await api(`/reviewer/rounds/${roundId}`));
    } catch (e) {
      onError(errText(e));
    }
  }, [roundId, onError]);

  useEffect(() => {
    load();
  }, [load]);

  if (!round) return <div className="panel muted">加载中…</div>;

  return (
    <>
      <div className="panel">
        <h2>
          轮次 #{round.id} · {round.name}{" "}
          {round.is_frozen ? (
            <span className="badge frozen">已冻结 · 作者身份已揭示</span>
          ) : (
            <span className="badge open">匿名阶段</span>
          )}
        </h2>
        <div className="muted">本轮全部稿件（仅显示匿名稿号）：</div>
        <div className="row" style={{ marginTop: 6 }}>
          {round.manuscripts.map((m) => (
            <span className="pill" key={m.manuscript_no}>
              #{m.manuscript_no}
              {round.is_frozen ? ` · ${m.author_name}` : ""} · r{m.revision_no}
            </span>
          ))}
        </div>
      </div>

      {round.assignments.map((a) => (
        <div className="panel" key={a.assignment_id}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h2 style={{ margin: 0 }}>
              稿件 #{a.manuscript_no} · 当前修订 r{a.revision_no}
            </h2>
            {a.submitted ? (
              <span className="badge done">已最终提交</span>
            ) : (
              <span className="badge pending">草稿中</span>
            )}
          </div>
          <p>
            <strong>{a.title}</strong>
          </p>
          <p className="muted">{a.abstract}</p>
          {round.is_frozen ? (
            <p>
              <strong>作者：</strong>
              {a.author_name ?? "—"}
            </p>
          ) : (
            <p className="muted">作者身份在整轮冻结前不可见。</p>
          )}
          <button className="ghost small" onClick={() => setOpenAssignment(a.assignment_id)}>
            {openAssignment === a.assignment_id ? "收起评审表" : "打开评审表"}
          </button>
          {openAssignment === a.assignment_id && (
            <ReviewForm
              assignmentId={a.assignment_id}
              currentRevision={a.revision_no}
              frozen={round.is_frozen}
              submitted={a.submitted}
              onSaved={load}
              onError={onError}
            />
          )}
        </div>
      ))}
    </>
  );
}

function ReviewForm({ assignmentId, currentRevision, frozen, submitted, onSaved, onError }) {
  const [detail, setDetail] = useState(null);
  const [revisionNo, setRevisionNo] = useState(currentRevision);
  const [score, setScore] = useState("");
  const [comment, setComment] = useState("");
  const [version, setVersion] = useState(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    const d = await api(`/reviewer/assignments/${assignmentId}`);
    setDetail(d);
    const existing = d.drafts.find((x) => x.revision_no === currentRevision);
    if (existing) {
      setScore(existing.score ?? "");
      setComment(existing.comment ?? "");
      setVersion(existing.version);
    } else {
      setScore("");
      setComment("");
      setVersion(null);
    }
    setRevisionNo(currentRevision);
  }

  useEffect(() => {
    load().catch((e) => onError(errText(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentRevision]);

  async function loadRevision(no) {
    setRevisionNo(no);
    const existing = detail.drafts.find((x) => x.revision_no === no);
    if (existing) {
      setScore(existing.score ?? "");
      setComment(existing.comment ?? "");
      setVersion(existing.version);
    } else {
      setScore("");
      setComment("");
      setVersion(null);
    }
  }

  async function saveDraft() {
    setBusy(true);
    setMsg("");
    try {
      const saved = await api(`/reviewer/assignments/${assignmentId}/draft`, {
        method: "PUT",
        body: {
          revision_no: revisionNo,
          score: score === "" ? null : Number(score),
          comment,
          expected_version: version,
        },
      });
      setVersion(saved.version);
      setMsg(`草稿已保存（版本 ${saved.version}，修订 r${saved.revision_no}）`);
      await onSaved();
      await load();
    } catch (e) {
      onError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  async function submitFinal() {
    if (!window.confirm("最终提交后内容将不可修改。确认提交？")) return;
    setBusy(true);
    setMsg("");
    try {
      await api(`/reviewer/assignments/${assignmentId}/submit`, {
        method: "POST",
        body: {
          revision_no: currentRevision,
          score: score === "" ? null : Number(score),
          comment,
        },
      });
      setMsg("已最终提交，等待管理员冻结整轮。");
      await onSaved();
    } catch (e) {
      onError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  if (!detail) return <div className="muted">加载中…</div>;

  const locked = frozen || submitted;

  return (
    <div style={{ marginTop: 12 }}>
      {msg && <div className="ok-msg">{msg}</div>}
      <label>草稿修订号（可按任意修订号留存草稿；最终提交须对应当前修订 r{currentRevision}）</label>
      <div className="row">
        {Array.from({ length: currentRevision }, (_, i) => i + 1).map((no) => (
          <button
            key={no}
            type="button"
            className={revisionNo === no ? "" : "ghost"}
            onClick={() => loadRevision(no)}
            disabled={locked}
          >
            r{no}
          </button>
        ))}
        {version !== null && <span className="muted">草稿版本 v{version}</span>}
      </div>
      <label>评分（0–100，最终提交必填）</label>
      <input
        type="number"
        min={0}
        max={100}
        value={score}
        disabled={locked}
        onChange={(e) => setScore(e.target.value)}
      />
      <label>评审意见</label>
      <textarea value={comment} disabled={locked} onChange={(e) => setComment(e.target.value)} />
      {!locked && (
        <div className="row" style={{ marginTop: 10 }}>
          <button onClick={saveDraft} disabled={busy}>
            保存草稿（修订 r{revisionNo}）
          </button>
          <button className="ghost" onClick={submitFinal} disabled={busy || revisionNo !== currentRevision}>
            {revisionNo !== currentRevision
              ? `请切到当前修订 r${currentRevision} 再最终提交`
              : "最终提交"}
          </button>
        </div>
      )}
      {locked && <p className="muted">{frozen ? "整轮已冻结，内容不可修改。" : "你已最终提交，内容不可修改。"}</p>}
    </div>
  );
}
