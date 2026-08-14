"use client";

import { useState } from "react";
import type { SOPCandidate } from "@/lib/types";
import { Badge } from "@/components/UI";

export function SOPReview({ item }: { item: SOPCandidate }) {
  const [status, setStatus] = useState(item.session_status);
  async function review(next: string) {
    const response = await fetch(`/feedback/api/v1/sop-candidates/${item.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: next }) });
    if (response.ok) setStatus(next);
  }
  return <article className="panel sop-card"><div className="panel-head"><div><Badge tone="amber">{status}</Badge><h2>{item.title}</h2></div><span className="muted">{item.generation_source} · session sandbox</span></div><p>{item.applicable_when}</p><ol>{item.steps.map((step) => <li key={step}>{step}</li>)}</ol><div className="warning"><strong>禁止动作</strong>{item.prohibited_actions.join("；")}</div><div className="ticket-chips">{item.evidence_ticket_ids.map((id) => <span key={id}>{id}</span>)}</div><div className="actions"><button onClick={() => review("accepted")}>本次接受</button><button className="secondary" onClick={() => review("rejected")}>本次拒绝</button></div></article>;
}
