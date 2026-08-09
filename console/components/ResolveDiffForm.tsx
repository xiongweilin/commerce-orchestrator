"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";

const NOT_READY_CODES = new Set([
  "not_found",
  "method_not_allowed",
  "not_implemented",
  "http_404",
  "http_405",
  "http_501",
]);

/**
 * 差异解决表单：POST /v1/reconciliations/{runId}/diffs/{diffId}/resolve。
 * 后端 resolve 接口可能尚未接线：404/405/501 或对应错误码时展示“接口未就绪”，不崩溃。
 */
export default function ResolveDiffForm({ runId, diffId }: { runId: string; diffId: string }) {
  const router = useRouter();
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState<{
    kind: "success" | "error" | "not_ready";
    text: string;
  } | null>(null);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSubmitting(true);
    setMessage(null);
    try {
      await api.post(`/v1/reconciliations/${runId}/diffs/${diffId}/resolve`, {
        note: note.trim(),
      });
      setMessage({ kind: "success", text: "已提交解决备注，列表已刷新。" });
      window.setTimeout(() => router.refresh(), 500);
    } catch (err) {
      if (err instanceof ApiError) {
        if (
          err.status === 404 ||
          err.status === 405 ||
          err.status === 501 ||
          NOT_READY_CODES.has(err.code)
        ) {
          setMessage({
            kind: "not_ready",
            text: `接口未就绪：后端尚未实现该接口（${err.message}）。`,
          });
        } else {
          setMessage({
            kind: "error",
            text: err.correlationId ? `${err.message}（关联ID：${err.correlationId}）` : err.message,
          });
        }
      } else {
        setMessage({ kind: "error", text: "提交失败，请稍后重试。" });
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form className="resolve-form" onSubmit={handleSubmit}>
      <div className="resolve-row">
        <textarea
          className="input textarea"
          placeholder="解决备注（必填）"
          rows={2}
          value={note}
          onChange={(event) => setNote(event.target.value)}
        />
        <button type="submit" className="btn btn-primary" disabled={submitting || !note.trim()}>
          {submitting ? "提交中…" : "解决"}
        </button>
      </div>
      {message && message.kind === "success" && <div className="success-box">{message.text}</div>}
      {message && message.kind === "not_ready" && (
        <div className="error-box warning">{message.text}</div>
      )}
      {message && message.kind === "error" && <div className="error-box">{message.text}</div>}
    </form>
  );
}
