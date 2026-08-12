/**
 * GET /api/me：返回当前用户（供客户端组件显示）。
 * 无会话 -> 401；后端 /v1/me 不可用 -> 502 backend_not_ready。
 */

import { NextResponse } from "next/server";
import { getServerToken } from "@/lib/server-auth";
import { SERVER_API_BASE } from "@/lib/session";
import {
  clearSessionCookies,
  errorJson,
  isSessionMockEnabled,
  mockUser,
} from "@/lib/session-server";

export const dynamic = "force-dynamic";

export async function GET() {
  const token = await getServerToken();
  if (!token) {
    return errorJson(401, "unauthenticated", "未登录：缺少会话");
  }

  if (isSessionMockEnabled()) {
    return NextResponse.json({ user: mockUser() });
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${SERVER_API_BASE}/v1/me`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });
  } catch {
    return errorJson(502, "backend_unreachable", `无法连接后端 ${SERVER_API_BASE}`);
  }

  if (upstream.status === 401 || upstream.status === 403) {
    await clearSessionCookies();
    return errorJson(401, "unauthenticated", "会话已失效，请重新登录");
  }
  if (!upstream.ok) {
    return errorJson(502, "backend_not_ready", `后端 /v1/me 不可用（HTTP ${upstream.status}）`);
  }

  const user = await upstream.json();
  return NextResponse.json({ user });
}
