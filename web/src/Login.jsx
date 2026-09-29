import { useState } from "react";
import { api, setSession } from "./api.js";

export default function Login({ onLoggedIn }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const data = await api("/auth/login", {
        method: "POST",
        form: { username, password },
      });
      setSession(data);
      onLoggedIn(data.role);
    } catch (err) {
      setError("用户名或密码错误");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-wrap">
      <form className="panel" onSubmit={submit}>
        <h2>同行评审系统 · 登录</h2>
        {error && <div className="error">{error}</div>}
        <label>用户名</label>
        <input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoFocus
          required
        />
        <label>密码</label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        <div style={{ marginTop: 14 }}>
          <button disabled={busy}>{busy ? "登录中…" : "登录"}</button>
        </div>
        <p className="muted" style={{ marginBottom: 0, marginTop: 14 }}>
          默认账号：管理员 <code>admin / admin123</code>
          <br />
          评审员 <code>r01…r12 / reviewer123</code>
        </p>
      </form>
    </div>
  );
}
