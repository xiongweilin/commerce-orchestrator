"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import type { CurrentUser } from "@/lib/types";

/**
 * 头部会话管理（替代旧的 localStorage Token 输入）：
 * - 未登录：输入一次 JWT -> POST /api/session（BFF 调后端 /v1/me 验证后设置 HttpOnly cookie）；
 * - 已登录：显示当前用户与角色，提供退出（DELETE /api/session）。
 * 客户端从不接触 JWT（HttpOnly cookie 不可读）。
 */
export default function SessionManager() {
  const router = useRouter();
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [checking, setChecking] = useState(true);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "error" | "ok"; text: string } | null>(null);

  const fetchMe = useCallback(async () => {
    setChecking(true);
    try {
      const res = await fetch("/api/me", { cache: "no-store" });
      const data = (await res.json().catch(() => null)) as { user?: CurrentUser } | null;
      setUser(res.ok && data?.user ? data.user : null);
    } catch {
      setUser(null);
    } finally {
      setChecking(false);
    }
  }, []);

  useEffect(() => {
    void fetchMe();
  }, [fetchMe]);

  const handleLogin = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!token.trim()) return;
    setBusy(true);
    setMessage(null);
    try {
      const res = await fetch("/api/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: token.trim() }),
      });
      const data = (await res.json().catch(() => null)) as { error?: { message?: string } } | null;
      if (!res.ok) {
        setMessage({ kind: "error", text: data?.error?.message ?? `登录失败（HTTP ${res.status}）` });
        setBusy(false);
        return;
      }
      setToken("");
      setMessage({ kind: "ok", text: "已登录，会话已建立。" });
      await fetchMe();
      router.refresh();
    } catch {
      setMessage({ kind: "error", text: "网络错误，无法连接会话接口。" });
    } finally {
      setBusy(false);
    }
  };

  const handleLogout = async () => {
    setBusy(true);
    try {
      await fetch("/api/session", { method: "DELETE" });
      setUser(null);
      setMessage(null);
      router.refresh();
    } catch {
      setMessage({ kind: "error", text: "退出失败，请稍后重试。" });
    } finally {
      setBusy(false);
    }
  };

  if (checking) {
    return <div className="session-manager muted">会话检查中…</div>;
  }

  if (user) {
    const displayName = user.username || user.email || user.id;
    return (
      <div className="session-manager">
        <span className="session-user" title={`ID：${user.id}`}>
          用户：{displayName}
        </span>
        <span className="session-roles" title="数据库权威角色">
          {user.roles.length > 0 ? user.roles.join("、") : "无角色"}
        </span>
        <button
          type="button"
          className="btn btn-secondary session-logout"
          onClick={handleLogout}
          disabled={busy}
        >
          {busy ? "退出中…" : "退出"}
        </button>
      </div>
    );
  }

  return (
    <form className="session-manager session-login" onSubmit={handleLogin}>
      <label htmlFor="session-token" className="muted">
        JWT
      </label>
      <input
        id="session-token"
        type="password"
        autoComplete="off"
        spellCheck={false}
        placeholder="一次性 JWT"
        value={token}
        onChange={(event) => setToken(event.target.value)}
      />
      <button type="submit" className="btn btn-primary" disabled={busy || !token.trim()}>
        {busy ? "登录中…" : "登录"}
      </button>
      {message && (
        <span className={`session-msg ${message.kind === "error" ? "msg-error" : "msg-ok"}`}>
          {message.text}
        </span>
      )}
    </form>
  );
}
