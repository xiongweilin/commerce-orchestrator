export type StatusTone = "info" | "success" | "warning" | "danger" | "neutral" | "highlight";

const STATUS_META: Record<string, { label: string; tone: StatusTone }> = {
  accepted: { label: "已受理", tone: "info" },
  queued: { label: "排队中", tone: "neutral" },
  pending: { label: "待处理", tone: "warning" },
  running: { label: "进行中", tone: "info" },
  in_progress: { label: "进行中", tone: "info" },
  processing: { label: "处理中", tone: "info" },
  completed: { label: "已完成", tone: "success" },
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
