/**
 * 后端 API 访问封装（BFF 安全会话，不再直接携带 JWT）。
 *
 * - 服务端组件：直连服务器私有 COMMERCE_API_BASE，Bearer 取自 HttpOnly 会话 cookie
 *   （见 lib/server-auth.ts getServerToken），token 仅存在于服务器内存/请求上下文。
 * - 客户端组件：访问同源 BFF `/api/backend[...]`；BFF 负责附加 Bearer、
 *   校验 CSRF（X-CSRF-Token + Origin）。客户端绝不接触 JWT。
 * - 幂等：需要时通过 idempotencyKey 选项附加 Idempotency-Key 请求头。
 * - 错误：统一解析 {error:{code,message,correlationId,details}} 并抛出 ApiError。
 */

import { SERVER_API_BASE } from "./session";

/** 同源 BFF 代理前缀（客户端唯一允许访问的后端入口）。 */
export const BFF_BASE = "/api/backend";

export interface ApiErrorPayload {
  code: string;
  message: string;
  correlationId?: string;
  details?: unknown;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly correlationId?: string;
  readonly details?: unknown;

  constructor(status: number, payload: ApiErrorPayload) {
    super(payload.message || (status > 0 ? `请求失败（HTTP ${status}）` : "网络错误"));
    this.name = "ApiError";
    this.status = status;
    this.code = payload.code || (status > 0 ? `http_${status}` : "network_error");
    this.correlationId = payload.correlationId;
    this.details = payload.details;
  }
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  /** 服务端场景显式传入的会话 JWT（见 lib/server-auth.ts）；客户端忽略该字段。 */
  token?: string | null;
  /** 幂等键：非空时附加 Idempotency-Key 请求头。 */
  idempotencyKey?: string;
  headers?: Record<string, string>;
}

/** 生成一次性幂等键（crypto.randomUUID，非安全上下文时降级）。 */
export function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

/** 读取非敏感 CSRF cookie（客户端专用；无会话时为空字符串，BFF 会返回 403）。 */
export function getCsrfToken(): string {
  if (typeof document === "undefined") return "";
  const parts = document.cookie.split(";").map((part) => part.trim());
  const match = parts.find((part) => part.startsWith("commerce_csrf="));
  if (!match) return "";
  const raw = match.slice("commerce_csrf=".length);
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

async function parseErrorPayload(response: Response): Promise<ApiErrorPayload> {
  const payload: ApiErrorPayload = {
    code: `http_${response.status}`,
    message: `请求失败（HTTP ${response.status}）`,
  };
  try {
    const raw: unknown = await response.json();
    if (raw && typeof raw === "object" && "error" in raw) {
      const errorBody = (raw as { error?: Partial<ApiErrorPayload> }).error;
      if (errorBody) {
        payload.code = errorBody.code || payload.code;
        payload.message = errorBody.message || payload.message;
        payload.correlationId = errorBody.correlationId;
        payload.details = errorBody.details;
      }
    }
  } catch {
    // 响应体不是 JSON 时保留默认错误信息
  }
  return payload;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const isClient = typeof window !== "undefined";
  const headers = new Headers(options.headers);
  if (options.idempotencyKey) headers.set("Idempotency-Key", options.idempotencyKey);
  if (options.body !== undefined) headers.set("Content-Type", "application/json");

  const method = options.method ?? (options.body !== undefined ? "POST" : "GET");
  let url: string;
  if (isClient) {
    url = `${BFF_BASE}${path}`;
    // 所有非 GET 的客户端请求携带 CSRF 令牌（BFF 校验 cookie 一致性 + Origin）。
    if (method !== "GET") headers.set("X-CSRF-Token", getCsrfToken());
  } else {
    url = `${SERVER_API_BASE}${path}`;
    const token = options.token ?? null;
    if (token) headers.set("Authorization", `Bearer ${token}`);
  }

  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      cache: "no-store",
    });
  } catch (err) {
    throw new ApiError(0, {
      code: "network_error",
      message: `无法连接后端服务 ${isClient ? "（同源 BFF）" : SERVER_API_BASE}：${
        err instanceof Error ? err.message : String(err)
      }`,
    });
  }

  if (!response.ok) {
    throw new ApiError(response.status, await parseErrorPayload(response));
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string, options: Omit<RequestOptions, "method" | "body"> = {}) =>
    request<T>(path, { ...options, method: "GET" }),
  post: <T>(path: string, body?: unknown, options: Omit<RequestOptions, "method"> = {}) =>
    request<T>(path, { ...options, method: "POST", body }),
};
