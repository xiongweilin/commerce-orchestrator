import { api } from "@/lib/api";
import { Badge, PageHead } from "@/components/UI";

export default async function TicketsPage() {
  const tickets = await api.tickets();
  return <><PageHead eyebrow="TICKET POOL" title="结构化工单池" description="每个分类、升级决定和证据片段都能回到原始工单。" />
    <div className="panel table-wrap"><table><thead><tr><th>工单</th><th>摘要</th><th>类型 / 区域</th><th>严重度</th><th>责任方</th><th>证据</th></tr></thead><tbody>
      {tickets.map((ticket) => <tr key={ticket.id}><td><strong>{ticket.ticket_id}</strong><small>{ticket.channel}</small></td><td>{ticket.analysis?.summary ?? "等待分析"}</td><td><span>{ticket.analysis?.problem_type}</span><small>{ticket.analysis?.product_area}</small></td><td><Badge tone={ticket.analysis?.severity === "critical" || ticket.analysis?.severity === "high" ? "red" : "default"}>{ticket.analysis?.severity ?? "queued"}</Badge></td><td>{ticket.analysis?.suggested_owner ?? "—"}</td><td className="evidence-cell">{ticket.analysis?.evidence_spans?.[0]?.quote ?? "—"}</td></tr>)}
    </tbody></table></div></>;
}

