/**
 * 服务端鉴权助手（仅限服务端组件 / Route Handler 使用）。
 * 会话 JWT 保存在 HttpOnly cookie（commerce_session）中，此处只读取、不落任何存储。
 */

import { cookies } from "next/headers";
import { SESSION_COOKIE } from "./session";
import { SERVER_API_BASE } from "./session";
import { isSessionMockEnabled, mockUser } from "./session-server";

/** 服务端读取 HttpOnly 会话 JWT；未登录返回 null。 */
export async function getServerToken(): Promise<string | null> {
  const store = await cookies();
  return store.get(SESSION_COOKIE)?.value ?? null;
}

/**
 * GET /v1/me 的最小客户端可见视图（数据库权威角色 + JWT 到期时间）。
 * 契约来源：整改计划 §四.1「GET /v1/me：返回当前 active 用户、数据库权威角色和 JWT 到期时间」；
 * 精确字段由 WP6 实现后核对（见 WP2-REPORT「待 WP6 联调」）。
 */
export interface CurrentUser {
  id: string;
  roles: string[];
  username?: string | null;
  email?: string | null;
  jwtExpiresAt?: string | null;
  [key: string]: unknown;
}

/**
 * 服务端组件读取当前用户（fail-closed）：
 * 无会话、后端不可达或后端 /v1/me 未实现（WP6 待办）时一律返回 null。
 */
export async function getServerUser(): Promise<CurrentUser | null> {
  const token = await getServerToken();
  if (!token) return null;
  if (isSessionMockEnabled()) {
    return mockUser();
  }
  try {
    const res = await fetch(`${SERVER_API_BASE}/v1/me`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });
    if (!res.ok) return null;
    const body = (await res.json()) as Record<string, unknown>;
    if (!body || typeof body !== "object") return null;
    const id = typeof body.id === "string" ? body.id : "";
    const roles = Array.isArray(body.roles)
      ? body.roles.filter((role): role is string => typeof role === "string")
      : [];
    if (!id) return null;
    return { ...body, id, roles } as CurrentUser;
  } catch {
    return null;
  }
}
