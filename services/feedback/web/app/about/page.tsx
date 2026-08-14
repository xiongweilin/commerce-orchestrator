import { PageHead } from "@/components/UI";

export default function AboutPage() {
  return <>
    <PageHead eyebrow="BOUNDARIES" title="这个作品证明什么，也不证明什么。" description="把模型建议、确定性约束、候选知识与正式规则分开，是系统本身的一部分。" />
    <section className="three-col">
      <article className="panel"><span className="kicker">已证明</span><h2>四工作流真实可运行</h2><p>结构化、问题簇叙事、候选 SOP、周报叙事均已发布并完成真实 API 回放；证据 ID 仍由服务端确定性复核。</p></article>
      <article className="panel"><span className="kicker amber">机制通过</span><h2>V7 合成门禁</h2><p>V7 锁定集九项已测门禁全部通过并完成作品集演示晋级；V5、V6 的失败记录仍保留，且 60 条合成评测不外推真实业务。</p></article>
      <article className="panel"><span className="kicker red">未证明</span><h2>真实业务收益</h2><p>没有独立人工审计、真实效率提升、客户满意度、生产稳定性或真实根因准确率证据。</p></article>
    </section>
    <section className="panel flow"><span>非结构化反馈</span><b>→</b><span>LLM 建议</span><b>→</b><span>确定性硬门</span><b>→</b><span>人工确认</span><b>→</b><span>候选改进</span></section>
  </>;
}
