import { ApiError } from "@/lib/api";

/** 把任意错误转为可读文本（优先取后端错误包中的 message/correlationId）。 */
export function apiErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    return err.correlationId ? `${err.message}（关联ID：${err.correlationId}）` : err.message;
  }
  if (err instanceof Error) return err.message;
  return String(err);
}

export default function ErrorBox({
  error,
  title = "出错了",
}: {
  error: unknown;
  title?: string;
}) {
  const message = typeof error === "string" ? error : apiErrorMessage(error);
  return (
    <div className="error-box" role="alert">
      <strong>{title}</strong>
      <p style={{ margin: "4px 0 0" }}>{message}</p>
    </div>
  );
}
