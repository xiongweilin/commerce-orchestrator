import { api } from "@/lib/api";
import { PageHead } from "@/components/UI";

type Observation = { text: string; evidence_ticket_ids: string[]; pending_cause?: string; recommended_action?: string };

export default async function ReportPage() {
  const report = await api.report();
  const payload = report.payload as { ticket_count: number; previous_ticket_count: number; observations: Observation[]; boundary: string };
  return <><PageHead eyebrow="WEEKLY REPORT" title="每条结论都有证据入口。" description={`事实观察、待确认原因和建议动作分开表达；当前叙事来源：${report.generation_source}。`} />
    <section className="report-hero"><div><span>THIS WEEK</span><strong>{payload.ticket_count}</strong><small>工单总数</small></div><div><span>PREVIOUS</span><strong>{payload.previous_ticket_count}</strong><small>上周期</small></div></section>
    <div className="stack">{payload.observations.map((item, index) => <article className="report-item" key={item.text}><span className="report-no">0{index + 1}</span><div><h2>观察</h2><p>{item.text}</p><h3>证据</h3><div className="ticket-chips">{item.evidence_ticket_ids.map((id) => <span key={id}>{id}</span>)}</div><h3>待确认原因</h3><p>{item.pending_cause ?? "尚无足够证据"}</p><h3>建议动作</h3><p>{item.recommended_action ?? "由业务人员确认"}</p></div></article>)}</div><p className="callout">{payload.boundary}</p>
  </>;
}
