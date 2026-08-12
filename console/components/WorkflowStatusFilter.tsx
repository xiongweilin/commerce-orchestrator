"use client";

import { useRouter } from "next/navigation";

const STATUS_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "", label: "全部状态" },
  { value: "accepted", label: "已受理" },
  { value: "running", label: "进行中" },
  { value: "awaiting_approval", label: "待审批" },
  { value: "completed", label: "已完成" },
  { value: "needs_reconciliation", label: "需对账" },
  { value: "failed", label: "失败" },
  { value: "cancelled", label: "已取消" },
];

/**
 * 工作流列表的状态筛选（通过 URL 查询参数驱动服务端渲染）。
 * 状态集合与整改计划 §二.1 一致：accepted/running/awaiting_approval/completed/
 * needs_reconciliation/failed/cancelled。
 */
export default function WorkflowStatusFilter({ current }: { current: string }) {
  const router = useRouter();
  const handleChange = (value: string) => {
    const params = new URLSearchParams();
    if (value) params.set("status", value);
    const qs = params.toString();
    router.push(qs ? `/workflows?${qs}` : "/workflows");
  };

  return (
    <select
      className="input filter-select"
      value={current}
      aria-label="按状态筛选工作流"
      onChange={(event) => handleChange(event.target.value)}
    >
      {STATUS_OPTIONS.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}
