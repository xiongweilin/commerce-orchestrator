export type StatusTone = "info" | "success" | "warning" | "danger" | "neutral" | "highlight";

const STATUS_META: Record<string, { label: string; tone: StatusTone }> = {
  // 销售订单
  received: { label: "已收货", tone: "info" },
  validated: { label: "已校验", tone: "info" },
  accepted: { label: "已受理", tone: "info" },
  odo_drafted: { label: "Odoo 草稿", tone: "neutral" },
  confirmed: { label: "已确认", tone: "info" },
  reserved: { label: "已预留", tone: "info" },
  picking: { label: "拣货中", tone: "warning" },
  shipped: { label: "已发货", tone: "success" },
  invoiced: { label: "已开票", tone: "success" },
  // 退货
  requested: { label: "已申请", tone: "info" },
  eligibility_review: { label: "资格审核", tone: "warning" },
  authorized: { label: "已授权", tone: "info" },
  inspected: { label: "已检验", tone: "info" },
  disposition_approved: { label: "处置已批准", tone: "success" },
  credit_note_posted: { label: "贷项已过账", tone: "success" },
  refund_pending: { label: "退款处理中", tone: "warning" },
  refund_succeeded: { label: "退款成功", tone: "success" },
  // 采购
  demand_detected: { label: "需求已识别", tone: "info" },
  rfq_draft: { label: "询价草稿", tone: "neutral" },
  pending_approval: { label: "待审批", tone: "warning" },
  awaiting_approval: { label: "待审批", tone: "warning" },
  po_confirmed: { label: "PO 已确认", tone: "success" },
  partially_received: { label: "部分收货", tone: "warning" },
  bill_posted: { label: "账单已过账", tone: "success" },
  // 通用流程状态
  queued: { label: "排队中", tone: "neutral" },
  pending: { label: "待处理", tone: "warning" },
  running: { label: "进行中", tone: "info" },
  in_progress: { label: "进行中", tone: "info" },
  processing: { label: "处理中", tone: "info" },
  completed: { label: "已完成", tone: "success" },
  needs_reconciliation: { label: "需对账", tone: "highlight" },
  planned: { label: "已计划", tone: "info" },
  dispatched: { label: "已派发", tone: "info" },
  outcome_unknown: { label: "结果未知", tone: "highlight" },
  succeeded: { label: "成功", tone: "success" },
  success: { label: "成功", tone: "success" },
  approved: { label: "已批准", tone: "success" },
  resolved: { label: "已解决", tone: "success" },
  matched: { label: "一致", tone: "success" },
  reconciled: { label: "已对平", tone: "success" },
  failed: { label: "失败", tone: "danger" },
  error: { label: "错误", tone: "danger" },
  rejected: { label: "已拒绝", tone: "danger" },
  cancelled: { label: "已取消", tone: "neutral" },
  canceled: { label: "已取消", tone: "neutral" },
  created: { label: "已创建", tone: "neutral" },
  closed: { label: "已关闭", tone: "neutral" },
  in_payment: { label: "收付款中", tone: "warning" },
  manual_reconciliation: { label: "人工对账", tone: "highlight" },
  unmatched: { label: "不一致", tone: "warning" },
  difference: { label: "有差异", tone: "warning" },
  expired: { label: "已过期", tone: "neutral" },
};

export function statusTone(status: string): StatusTone {
  return STATUS_META[status.toLowerCase()]?.tone ?? "neutral";
}

export default function StatusBadge({
  status,
  label,
}: {
  status: string;
  label?: string;
}) {
  const meta = STATUS_META[status.toLowerCase()];
  const tone = meta?.tone ?? "neutral";
  return (
    <span className={`badge badge-${tone}`} title={status}>
      {label ?? meta?.label ?? status}
    </span>
  );
}
