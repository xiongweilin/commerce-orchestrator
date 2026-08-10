"use client";

import { useEffect, useState } from "react";
import { API_BASE } from "@/lib/api";
import { formatTime } from "@/lib/format";

/** 概览页实时后端健康状态（GET /healthz）。 */
export default function HealthStatus() {
  const [state, setState] = useState<"checking" | "ok" | "down">("checking");
  const [checkedAt, setCheckedAt] = useState<string | null>(null);
  const [detail, setDetail] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function check() {
      try {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 5000);
        const res = await fetch(`${API_BASE}/healthz`, { signal: controller.signal, cache: "no-store" });
        clearTimeout(timer);
        if (cancelled) return;
        setState(res.ok ? "ok" : "down");
        setCheckedAt(new Date().toISOString());
        setDetail(res.ok ? `HTTP ${res.status}` : `HTTP ${res.status}`);
      } catch {
        if (cancelled) return;
        setState("down");
        setCheckedAt(new Date().toISOString());
        setDetail("无法连接");
      }
    }
    check();
    const interval = setInterval(check, 30000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  const label =
    state === "checking" ? "检查中…" : state === "ok" ? "正常" : "异常";

  return (
    <p>
      实时健康状态：
      <span className={`badge badge-${state === "ok" ? "success" : state === "down" ? "danger" : "warning"}`}>
        {label}
      </span>
      {detail && <span>（{detail}）</span>}
      {checkedAt && <span> · 检查时间 {formatTime(checkedAt)}（每 30 秒刷新）</span>}
    </p>
  );
}
