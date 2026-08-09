"use client";

import { useEffect, useState } from "react";
import { TOKEN_COOKIE, TOKEN_KEY } from "@/lib/api";

/**
 * 头部 Token 输入框：
 * - 保存在浏览器 localStorage（键 commerce_token）；
 * - 同步写入同名 cookie，供服务端组件在页面请求时携带 token。
 */
export default function TokenInput() {
  const [value, setValue] = useState("");

  useEffect(() => {
    let stored: string | null = null;
    try {
      stored = window.localStorage.getItem(TOKEN_KEY);
    } catch {
      // localStorage 不可用（如隐私模式）时忽略
    }
    setValue(stored ?? "");
  }, []);

  const handleChange = (next: string) => {
    setValue(next);
    try {
      window.localStorage.setItem(TOKEN_KEY, next);
    } catch {
      // 忽略写入失败
    }
    document.cookie = `${TOKEN_COOKIE}=${encodeURIComponent(next)}; path=/; SameSite=Lax`;
  };

  return (
    <div className="token-input">
      <label htmlFor="commerce-token">Token</label>
      <input
        id="commerce-token"
        type="password"
        autoComplete="off"
        spellCheck={false}
        placeholder="Bearer Token"
        value={value}
        onChange={(event) => handleChange(event.target.value)}
      />
      {value && (
        <button
          type="button"
          className="token-clear"
          title="清除 Token"
          onClick={() => handleChange("")}
        >
          ✕
        </button>
      )}
    </div>
  );
}
