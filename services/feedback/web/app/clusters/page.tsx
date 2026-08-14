import { api } from "@/lib/api";
import { Badge, PageHead } from "@/components/UI";

export default async function ClustersPage() {
  const clusters = await api.clusters();
  return <><PageHead eyebrow="ISSUE CLUSTERS" title="重复问题与趋势" description="展示问题成员、代表证据和错误合并风险；聚类标题不等于已证实根因。" />
    <div className="card-grid">{clusters.map((cluster) => <article className="cluster-card" key={cluster.id}><div className="card-top"><Badge tone={cluster.trend === "rising" ? "red" : "green"}>{cluster.trend}</Badge><span>{cluster.member_count} tickets · {cluster.narrative_source}</span></div><h2>{cluster.title}</h2><p>{cluster.summary}</p><dl><div><dt>责任方</dt><dd>{cluster.suggested_owner}</dd></div><div><dt>严重度</dt><dd>{cluster.severity}</dd></div></dl><div className="ticket-chips">{cluster.representative_ticket_ids.map((id) => <span key={id}>{id}</span>)}</div></article>)}</div>
  </>;
}
