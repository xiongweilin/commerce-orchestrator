import { api } from "@/lib/api";
import { Badge, Metric, PageHead } from "@/components/UI";

export default async function Dashboard() {
  const [tickets, clusters, sops] = await Promise.all([api.tickets(), api.clusters(), api.sops()]);
  const escalations = tickets.filter((item) => item.analysis?.needs_escalation).length;
  const reviews = tickets.filter((item) => item.analysis?.review_status === "needs_review").length;
  return <>
    <PageHead eyebrow="WEEKLY SIGNAL DESK" title="把一线噪音，变成可以行动的问题。" description="LLM 负责摘要与分类建议；规则负责证据定位、责任路由、严重度和人工复核。" />
    <section className="metrics-grid">
      <Metric label="结构化工单" value={tickets.length} detail="合成演示数据" />
      <Metric label="问题簇" value={clusters.length} detail="向量聚类 + 可追溯证据" />
      <Metric label="升级清单" value={escalations} detail="规则裁决，不由情绪决定" />
      <Metric label="待人工复核" value={reviews} detail="冲突与证据不足" />
    </section>
    <section className="two-col">
      <div className="panel"><div className="panel-head"><h2>高频问题簇</h2><Badge tone="green">TOP 5</Badge></div>
        <div className="stack">{clusters.slice(0, 5).map((cluster, index) => <article className="rank-row" key={cluster.id}>
          <span className="rank">0{index + 1}</span><div><strong>{cluster.title}</strong><small>{cluster.suggested_owner} · {cluster.representative_ticket_ids.join(" / ")}</small></div><b>{cluster.member_count}</b>
        </article>)}</div>
      </div>
      <div className="panel boundary-panel"><div className="panel-head"><h2>自动化边界</h2><Badge tone="amber">HUMAN GATE</Badge></div>
        <ul className="boundary-list"><li><b>模型</b><span>摘要、类别建议、待确认原因</span></li><li><b>规则</b><span>证据 offset、责任方、严重度、升级</span></li><li><b>人工</b><span>纠正结果、审核候选 SOP</span></li></ul>
        <p className="callout">候选知识不会自动进入正式 SOP。公网审核只存在当前会话。</p>
      </div>
    </section>
    <section className="panel"><div className="panel-head"><h2>候选 SOP</h2><span className="muted">{sops.length} 条等待业务确认</span></div>
      {sops.slice(0, 3).map((item) => <div className="sop-line" key={item.id}><Badge tone="amber">PENDING</Badge><strong>{item.title}</strong><span>{item.evidence_ticket_ids.join(" · ")}</span></div>)}
    </section>
  </>;
}

