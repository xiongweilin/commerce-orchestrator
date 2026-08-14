import { api } from "@/lib/api";
import { Empty, PageHead } from "@/components/UI";
import { SOPReview } from "./review";

export default async function SOPPage() {
  const candidates = await api.sops();
  return <><PageHead eyebrow="CANDIDATE SOP" title="候选流程，不污染正式知识。" description="候选只来自达到频次和严重度门槛的问题簇；接受与拒绝仅保存于当前演示会话。" />
    {candidates.length ? <div className="stack">{candidates.map((item) => <SOPReview key={item.id} item={item} />)}</div> : <Empty>当前聚类没有达到候选 SOP 触发条件。</Empty>}
  </>;
}

