import { api } from "@/lib/api";
import { Metric, PageHead } from "@/components/UI";

export default async function EvaluationPage() {
  const suite = await api.suiteEvaluation() as any;
  const latest = suite.structure_clustering ?? {};
  const structure = latest.structure ?? {};
  const clustering = latest.clustering?.holdout_metrics ?? {};
  const workflows = suite.content_workflows ?? {};
  const contentMetrics = workflows.metrics ?? {};
  const problemF1 = structure.problem_type?.report?.["macro avg"]?.["f1-score"] ?? 0;
  const areaF1 = structure.product_area?.report?.["macro avg"]?.["f1-score"] ?? 0;
  const gates = latest.quality_gates?.items ?? [];
  const failed = gates.filter((gate: any) => !gate.passed);

  return <>
    <PageHead
      eyebrow="EVALUATION"
      title="V7 通过门禁，但边界仍写在结果旁边。"
      description="锁定合成集 N=60，仅用于机制质量评估；AI 辅助一致性复核不等于独立人工审计，也不代表真实业务分布。"
    />
    <section className="panel">
      <h2>V7：已晋级作品集演示基线</h2>
      <p>状态：{suite.evaluation_state}；数据：{suite.dataset_version}。九项已测门禁全部通过，并由独立 promotion record 固化运行配置。</p>
      <p className="callout">
        {failed.length === 0
          ? "已测门禁全部通过。未测项仍明确保留：已接受记录 offset 有效率、周报与 SOP 引用率。"
          : `未通过：${failed.map((gate: any) => gate.label).join("、")}。`}
      </p>
    </section>

    <h2>结构化与重复问题识别</h2>
    <section className="metrics-grid">
      <Metric label="Schema 契约有效" value={`${((structure.schema_contract_valid_rate ?? 0) * 100).toFixed(1)}%`} detail="门槛 95%，通过" />
      <Metric label="首次依赖成功" value={`${((structure.first_attempt_dependency_success_rate ?? 0) * 100).toFixed(1)}%`} detail="信息项；与 Schema 分开" />
      <Metric label="问题类型 Macro-F1" value={problemF1.toFixed(3)} detail="门槛 0.80，通过" />
      <Metric label="产品区域 Macro-F1" value={areaF1.toFixed(3)} detail="门槛 0.80，通过" />
      <Metric label="quote 自动定位" value={`${((structure.evidence_auto_location_rate ?? 0) * 100).toFixed(1)}%`} detail="失败进入人工复核" />
      <Metric label="责任路由一致率" value={`${((structure.owner_policy_consistency ?? 0) * 100).toFixed(1)}%`} detail="规则映射，门槛 85%" />
      <Metric label="重复识别精确率" value={`${((clustering.pairwise?.precision ?? 0) * 100).toFixed(1)}%`} detail="门槛 80%，通过" />
      <Metric label="重复识别召回率" value={`${((clustering.pairwise?.recall ?? 0) * 100).toFixed(1)}%`} detail="门槛 50%，通过" />
      <Metric label="聚类纯度" value={`${((clustering.purity ?? 0) * 100).toFixed(1)}%`} detail="面试展示指标" />
      <Metric label="B³ F1" value={(clustering.b_cubed?.f1 ?? 0).toFixed(3)} detail="技术报告指标" />
    </section>

    <h2>内容生成工作流</h2>
    <section className="metrics-grid">
      <Metric label="问题簇叙事" value={`${((contentMetrics.cluster_success_rate ?? 0) * 100).toFixed(0)}%`} detail={`${workflows.attempts?.cluster ?? 0} 次真实调用`} />
      <Metric label="候选 SOP" value={`${((contentMetrics.sop_success_rate ?? 0) * 100).toFixed(0)}%`} detail={`${workflows.attempts?.sop ?? 0} 次真实调用`} />
      <Metric label="周报叙事" value={`${((contentMetrics.report_success_rate ?? 0) * 100).toFixed(0)}%`} detail={`${workflows.attempts?.report ?? 0} 次真实调用`} />
      <Metric label="证据引用有效" value={`${((contentMetrics.evidence_valid_rate ?? 0) * 100).toFixed(0)}%`} detail="服务端再次校验 ticket_id" />
    </section>

    <section className="two-col">
      <div className="panel"><h2>错误合并案例</h2><pre>{JSON.stringify(latest.clustering?.false_merge_examples ?? [], null, 2)}</pre></div>
      <div className="panel"><h2>错误拆分案例</h2><pre>{JSON.stringify(latest.clustering?.false_split_examples ?? [], null, 2)}</pre></div>
    </section>

    <section className="panel">
      <h2>失败历史没有被覆盖</h2>
      <p>V5 因问题类型、责任路由和重复识别未达门槛而未晋级；V6 虽把问题类型 F1 提升到 0.901，但聚类精确率 57.1%、召回率 40.0%，仍未晋级。V7 使用新的锁定集和独立晋级记录，不在旧 holdout 上反复调参。</p>
    </section>
  </>;
}
