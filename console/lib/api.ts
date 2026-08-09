/**
 * 后端 API 访问封装（可同时被服务端组件与客户端组件使用）。
 * - base URL：NEXT_PUBLIC_API_BASE || http://localhost:8000
 * - 认证：浏览器环境下自动附加 Authorization: Bearer <localStorage commerce_token>
 * - 幂等：需要时通过 idempotencyKey 选项附加 Idempotency-Key 请求头
 * - 错误：统一解析 {error:{code,message,correlationId,details}} 并抛出 ApiError
 */

export const API_BASE: string =
  process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

/** localStorage 键名（与 TokenInput 组件共用）。 */
export const TOKEN_KEY = "commerce_token";
/** 服务端组件通过同名 cookie 读取 token（客户端写入，便于 SSR 携带）。 */
export const TOKEN_COOKIE = "commerce_token";

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

function getCookieValue(name: string): string | null {
  if (typeof document === "undefined") return null;
  const parts = document.cookie.split(";").map((part) => part.trim());
  const match = parts.find((part) => part.startsWith(`${name}=`));
  if (!match) return null;
  const raw = match.slice(name.length + 1);
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

/**
 * 读取当前 token：
 * - 浏览器：优先 localStorage（commerce_token），兜底同名 cookie；
 * - 服务端：返回 null（服务端页面请通过 lib/server-auth.ts 的 getServerToken 显式传入 token）。
 */
export function getStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(TOKEN_KEY) ?? getCookieValue(TOKEN_COOKIE);
  } catch {
    return getCookieValue(TOKEN_COOKIE);
  }
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  /** 显式传入 token（服务端场景）；缺省时浏览器环境自动从 localStorage 读取。 */
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

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const token = options.token ?? getStoredToken();
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.idempotencyKey) headers.set("Idempotency-Key", options.idempotencyKey);
  if (options.body !== undefined) headers.set("Content-Type", "application/json");

  const method = options.method ?? (options.body !== undefined ? "POST" : "GET");

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      cache: "no-store",
    });
  } catch (err) {
    throw new ApiError(0, {
      code: "network_error",
      message: `无法连接后端服务 ${API_BASE}：${err instanceof Error ? err.message : String(err)}`,
    });
  }

  if (!response.ok) {
    let payload: ApiErrorPayload = {
      code: `http_${response.status}`,
      message: `请求失败（HTTP ${response.status}）`,
    };
    try {
      const raw: unknown = await response.json();
      if (raw && typeof raw === "object" && "error" in raw) {
        const errorBody = (raw as { error?: Partial<ApiErrorPayload> }).error;
        if (errorBody) {
          payload = {
            code: errorBody.code || payload.code,
            message: errorBody.message || payload.message,
            correlationId: errorBody.correlationId,
            details: errorBody.details,
          };
        }
      }
    } catch {
      // 响应体不是 JSON 时保留默认错误信息
    }
    throw new ApiError(response.status, payload);
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
