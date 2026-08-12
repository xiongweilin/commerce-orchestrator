import Link from "next/link";
import { notFound } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { getServerToken } from "@/lib/server-auth";
import type { WorkflowDetail, WorkflowEffect, WorkflowEvent } from "@/lib/types";
import StatusBadge from "@/components/StatusBadge";
import ErrorBox from "@/components/ErrorBox";
import RefreshButton from "@/components/RefreshButton";
import DecisionForm from "@/components/DecisionForm";
import { formatTime, jsonText } from "@/lib/format";

export const dynamic = "force-dynamic";

function TimelineItem({ event }: { event: WorkflowEvent }) {
  const extras = Object.entries(event).filter(
    ([key]) => key !== "type" && key !== "occurredAt",
  );
  return (
    <li className="timeline-item">
      <div className="timeline-head">
        <span className="timeline-type">{event.type}</span>
        <span className="timeline-time">{formatTime(event.occurredAt)}</span>
      </div>
      {extras.length > 0 && (
        <pre className="timeline-extras">
          {extras
            .map(([key, value]) => `${key}: ${typeof value === "string" ? value : jsonText(value)}`)
            .join("\n")}
        </pre>
      )}
    </li>
  );
}

function EffectRow({ effect }: { effect: WorkflowEffect }) {
  return (
    <tr>
      <td className="mono">{effect.operation}</td>
      <td>
        <StatusBadge status={effect.status} />
      </td>
      <td className="mono">{effect.remoteReference ?? "—"}</td>
      <td>{effect.attempt ?? "—"}</td>
      <td title={effect.errorDetail ?? undefined}>{effect.errorDetail ?? "—"}</td>
    </tr>
  );
}

export default async function WorkflowDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  let workflow: WorkflowDetail | null = null;
  let error: string | null = null;
  try {
    workflow = await api.get<WorkflowDetail>(`/v1/workflows/${encodeURIComponent(id)}`, {
      token: await getServerToken(),
    });
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      notFound();
    }
    error =
      err instanceof ApiError
        ? err.correlationId
          ? `${err.message}（关联ID：${err.correlationId}）`
          : err.message
        : "无法连接后端服务";
  }

  if (!workflow) {
    return (
      <div className="page">
        <h1>工作流详情</h1>
        <ErrorBox error={error ?? "未知错误"} title="加载失败" />
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>工作流详情</h1>
          <p className="mono">ID：{workflow.workflowId}</p>
        </div>
        <div className="page-actions">
          <Link className="btn btn-secondary" href="/workflows">
            返回列表
          </Link>
          <RefreshButton />
        </div>
      </div>

      <div className="card">
        <div className="detail-header">
          <StatusBadge status={workflow.status} />
          <span className="detail-label">类型</span>
          <span>{workflow.type}</span>
          <span className="detail-label">当前步骤</span>
          <span>{workflow.currentStep ?? "—"}</span>
          <span className="detail-label">版本</span>
          <span className="mono">{workflow.expectedWorkflowVersion}</span>
          <span className="detail-label">创建时间</span>
          <span>{formatTime(workflow.createdAt)}</span>
          <span className="detail-label">更新时间</span>
          <span>{formatTime(workflow.updatedAt)}</span>
        </div>
      </div>

      {(workflow.input !== undefined ||
        workflow.result !== undefined ||
        workflow.error !== undefined) && (
        <div className="card">
          <h2>输入 / 结果 / 错误</h2>
          <div className="kv-grid">
            {workflow.input !== undefined && (
              <details>
                <summary>输入（input）</summary>
                <pre>{jsonText(workflow.input)}</pre>
              </details>
            )}
            {workflow.result !== undefined && (
              <details>
                <summary>结果（result）</summary>
                <pre>{jsonText(workflow.result)}</pre>
              </details>
            )}
            {workflow.error !== undefined && (
              <details>
                <summary>错误（error）</summary>
                <pre className="pre-error">{jsonText(workflow.error)}</pre>
              </details>
            )}
          </div>
        </div>
      )}

      <div className="card">
        <h2>事件时间线（{workflow.events.length}）</h2>
        {workflow.events.length === 0 ? (
          <p className="empty">暂无事件</p>
        ) : (
          <ol className="timeline">
            {workflow.events.map((event, index) => (
              <TimelineItem key={index} event={event} />
            ))}
          </ol>
        )}
      </div>

      <div className="card">
        <h2>效果台账（effects ledger）</h2>
        {workflow.effects.length === 0 ? (
          <p className="empty">暂无效果记录</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>操作（operation）</th>
                  <th>状态</th>
                  <th>远端引用（remoteReference）</th>
                  <th>尝试次数（attempt）</th>
                  <th>错误详情（errorDetail）</th>
                </tr>
              </thead>
              <tbody>
                {workflow.effects.map((effect, index) => (
                  <EffectRow key={index} effect={effect} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card">
        <h2>工作项（{workflow.workItems.length}）</h2>
        {workflow.workItems.length === 0 ? (
          <p className="empty">暂无工作项</p>
        ) : (
          <div className="work-items">
            {workflow.workItems.map((item) => (
              <div key={item.workItemId} className="work-item">
                <div className="work-item-head">
                  <strong>{item.title || item.workItemId}</strong>
                  <StatusBadge status={item.status} />
                </div>
                <div className="work-item-meta">
                  <span className="detail-label">类型</span>
                  <span>{item.kind}</span>
                  <span className="detail-label">工作项ID</span>
                  <span className="mono">{item.workItemId}</span>
                  <span className="detail-label">到期</span>
                  <span>{formatTime(item.expiresAt)}</span>
                </div>
                {item.status.toLowerCase() === "pending" ? (
                  <DecisionForm
                    workItemId={item.workItemId}
                    expectedWorkflowVersion={
                      item.expectedWorkflowVersion ?? item.expectedVersion ?? 1
                    }
                  />
                ) : null}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
