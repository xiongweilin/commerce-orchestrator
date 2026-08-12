/**
 * BFF 后端代理（/v1 前缀白名单）：
 * - 仅代理 /v1 下一级前缀命中 ALLOWED_PREFIXES 的路径（与 backend/app/api/v1/ 实际注册路由
 *   保持一致；后端新增路由时必须同步登记，防止未来管理接口被自动暴露）；
 *   webhook 入口禁止经 BFF 转发（防止伪造 Shopify 回调）。
 * - 仅允许约定的 HTTP 方法（GET/POST/PUT/PATCH/DELETE）。
 * - 非 GET 请求强制校验：X-CSRF-Token 与 commerce_csrf cookie 一致（常量时间比较）+ Origin 等于 console origin。
 * - 服务器私有 COMMERCE_API_BASE；JWT 只存在于 HttpOnly 会话 cookie 中，由 BFF 附加为 Bearer。
 */

import { NextRequest } from "next/server";
import { cookies } from "next/headers";
import { timingSafeEqual } from "node:crypto";
import { CSRF_COOKIE, SERVER_API_BASE, SESSION_COOKIE } from "@/lib/session";
import { errorJson } from "@/lib/session-server";

export const dynamic = "force-dynamic";

const ALLOWED_METHODS = new Set(["GET", "POST", "PUT", "PATCH", "DELETE"]);
/** 允许代理的 /v1 一级路径前缀（按 backend/app/api/v1/ 各 router 实际注册路径登记）。 */
const ALLOWED_PREFIXES = new Set([
  "me",
  "work-items",
  "workflows",
  "catalog-revisions",
  "listing-publications",
  "procurements",
  "returns",
  "reconciliations",
  "return-cases",
  "sales-orders",
  "ops",
  "webhooks",
]);
/** 禁止经 BFF 代理的路径（webhook 只接受 Shopify 直连签名校验）。 */
const DENY_PATHS = new Set(["/v1/webhooks/shopify"]);

function csrfTokensEqual(a: string, b: string): boolean {
  const left = Buffer.from(a);
  const right = Buffer.from(b);
  if (left.length !== right.length) return false;
  return timingSafeEqual(left, right);
}

async function handle(request: NextRequest, paramsPromise: Promise<{ path: string[] }>) {
  const { path } = await paramsPromise;
  const apiPath = `/${path.join("/")}`;

  if (
    !apiPath.startsWith("/v1/") ||
    !ALLOWED_PREFIXES.has(apiPath.split("/")[2] ?? "") ||
    DENY_PATHS.has(apiPath)
  ) {
    return errorJson(404, "not_found", "BFF 仅代理允许的后端路径（/v1 前缀白名单）");
  }

  const method = request.method;
  if (!ALLOWED_METHODS.has(method)) {
    return errorJson(405, "method_not_allowed", `方法 ${method} 不允许`);
  }

  const store = await cookies();
  const token = store.get(SESSION_COOKIE)?.value;
  if (!token) {
    return errorJson(401, "unauthenticated", "未登录：缺少会话");
  }

  if (method !== "GET") {
    const csrfCookie = store.get(CSRF_COOKIE)?.value;
    const csrfHeader = request.headers.get("x-csrf-token");
    if (!csrfCookie || !csrfHeader || !csrfTokensEqual(csrfCookie, csrfHeader)) {
      return errorJson(403, "csrf_mismatch", "CSRF 校验失败：X-CSRF-Token 与 cookie 不一致");
    }
    const origin = request.headers.get("origin");
    // 期望 Origin：显式 COMMERCE_CONSOLE_ORIGIN 优先，兼容 CONSOLE_ORIGIN 回退；
    // 未配置时按请求 Host 推导（浏览器 Origin 与 Host 一致）。
    // 注：dev 模式下 Next 内部 request.url 可能解析为 localhost，故不直接使用 URL.origin。
    const selfUrl = new URL(request.url);
    const host = request.headers.get("host");
    const expectedOrigin =
      process.env.COMMERCE_CONSOLE_ORIGIN ||
      process.env.CONSOLE_ORIGIN ||
      (host ? `${selfUrl.protocol}//${host}` : selfUrl.origin);
    if (!origin || origin !== expectedOrigin) {
      return errorJson(403, "origin_mismatch", "CSRF 校验失败：Origin 与 console origin 不一致");
    }
  }

  const headers: Record<string, string> = {
    Authorization: `Bearer ${token}`,
  };
  const contentType = request.headers.get("content-type");
  if (contentType) headers["Content-Type"] = contentType;
  const idempotencyKey = request.headers.get("idempotency-key");
  if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;

  let upstream: Response;
  try {
    upstream = await fetch(`${SERVER_API_BASE}${apiPath}`, {
      method,
      headers,
      body: method === "GET" ? undefined : await request.text(),
      cache: "no-store",
      redirect: "manual",
    });
  } catch {
    return errorJson(502, "backend_unreachable", `无法连接后端 ${SERVER_API_BASE}`);
  }

  const upstreamBody = await upstream.text();
  return new Response(upstreamBody, {
    status: upstream.status,
    headers: {
      "Content-Type": upstream.headers.get("content-type") || "application/json",
    },
  });
}

type Params = { params: Promise<{ path: string[] }> };

export async function GET(request: NextRequest, ctx: Params) {
  return handle(request, ctx.params);
}
export async function POST(request: NextRequest, ctx: Params) {
  return handle(request, ctx.params);
}
export async function PUT(request: NextRequest, ctx: Params) {
  return handle(request, ctx.params);
}
export async function PATCH(request: NextRequest, ctx: Params) {
  return handle(request, ctx.params);
}
export async function DELETE(request: NextRequest, ctx: Params) {
  return handle(request, ctx.params);
}
