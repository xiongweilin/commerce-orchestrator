/**
 * POST /api/session：接收一次 JWT，调用后端 /v1/me 验证后设置 HttpOnly 会话 + CSRF cookie。
 * DELETE /api/session：清除会话。
 *
 * 后端 /v1/me 已实现（WP6 落地）：本接口以 2xx 视为验证通过。
 * 开发模式可用 COMMERCE_SESSION_MOCK=1 跳过 /v1/me（仅 NODE_ENV !== "production" 时生效）。
 */

import { NextRequest, NextResponse } from "next/server";
import { SERVER_API_BASE } from "@/lib/session";
import {
  clearSessionCookies,
  errorJson,
  isSessionMockEnabled,
  mockUser,
  sessionMaxAgeSeconds,
  setSessionCookies,
} from "@/lib/session-server";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return errorJson(400, "validation_error", "请求体必须是 JSON");
  }
  const rawToken =
    body && typeof body === "object" && typeof (body as { token?: unknown }).token === "string"
      ? ((body as { token: string }).token as string).trim()
      : "";
  if (!rawToken) {
    return errorJson(400, "validation_error", "缺少 token 字段");
  }

  if (isSessionMockEnabled()) {
    await setSessionCookies(rawToken);
    return NextResponse.json({ user: mockUser(), expiresIn: sessionMaxAgeSeconds(rawToken) });
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${SERVER_API_BASE}/v1/me`, {
      headers: { Authorization: `Bearer ${rawToken}` },
      cache: "no-store",
    });
  } catch {
    return errorJson(502, "backend_unreachable", `无法连接后端 ${SERVER_API_BASE}`);
  }

  if (upstream.status === 404) {
    return errorJson(502, "backend_not_ready", "后端 /v1/me 不可用（HTTP 404）");
  }
  if (upstream.status === 401 || upstream.status === 403) {
    return errorJson(401, "unauthenticated", "Token 无效或已过期");
  }
  if (!upstream.ok) {
    return errorJson(upstream.status, "backend_error", `后端验证失败（HTTP ${upstream.status}）`);
  }

  const user = await upstream.json();
  await setSessionCookies(rawToken);
  return NextResponse.json({ user, expiresIn: sessionMaxAgeSeconds(rawToken) });
}

export async function DELETE() {
  await clearSessionCookies();
  return NextResponse.json({ ok: true });
}
