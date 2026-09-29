import { useEffect, useState } from "react";
import { api, clearSession, getMeta, getToken } from "./api.js";
import Login from "./Login.jsx";
import AdminApp from "./AdminApp.jsx";
import ReviewerApp from "./ReviewerApp.jsx";

export default function App() {
  const [role, setRole] = useState(() => getMeta()?.role ?? null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    // Validate any stored token against the API; 401 clears the session.
    if (!getToken()) {
      setReady(true);
      return;
    }
    api("/auth/me")
      .then((me) => setRole(me.role))
      .catch(() => setRole(null))
      .finally(() => setReady(true));
  }, []);

  function logout() {
    clearSession();
    setRole(null);
  }

  if (!ready) return null;
  if (!role) return <Login onLoggedIn={setRole} />;

  const meta = getMeta();
  return (
    <>
      <header className="topbar">
        <h1>同行评审系统</h1>
        <div className="who">
          <span>
            {meta?.display_name}（{role === "admin" ? "管理员" : "评审员"}）
          </span>
          <button className="ghost small" onClick={logout}>
            退出
          </button>
        </div>
      </header>
      <main>
        {role === "admin" ? <AdminApp /> : <ReviewerApp />}
      </main>
    </>
  );
}
