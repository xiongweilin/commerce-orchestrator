"use client";

import { useState } from "react";

export function TryForm() {
  const [message, setMessage] = useState("我们整个项目组都收不到任务到期提醒，已经联系两次，请帮忙确认。");
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit() {
    setBusy(true); setResult(null);
    await fetch("/feedback/api/v1/demo/sessions", { method: "POST" });
    const response = await fetch("/feedback/api/v1/tickets", { method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() }, body: JSON.stringify({ ticket_id: `LIVE-${Date.now()}`, user_type: "member", channel: "web_demo", message, created_at: new Date().toISOString(), current_status: "open" }) });
    const created = await response.json();
    if (!response.ok) { setResult(created); setBusy(false); return; }
    for (let attempt = 0; attempt < 20; attempt++) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      const job = await fetch(`/feedback/api/v1/jobs/${created.job_id}`).then((item) => item.json());
      if (["completed", "needs_review", "failed"].includes(job.status)) { setResult(job); setBusy(false); return; }
    }
    setResult({ detail: "processing_timeout" }); setBusy(false);
  }
  return <section className="try-grid"><div className="panel"><label htmlFor="message">工单正文</label><textarea id="message" maxLength={2000} value={message} onChange={(event) => setMessage(event.target.value)} /><div className="form-foot"><small>{message.length}/2000 · 真实邮箱和手机号会先脱敏</small><button disabled={busy || message.length < 3} onClick={submit}>{busy ? "分析中…" : "开始结构化"}</button></div></div><div className="panel result"><h2>结构化结果</h2>{result ? <pre>{JSON.stringify(result, null, 2)}</pre> : <p className="muted">结果将在这里显示。模型失败时系统明确转入人工复核，不伪造分类。</p>}</div></section>;
}

