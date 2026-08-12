/**
 * BFF 会话服务端助手（仅限 Route Handler / 服务端组件使用，依赖 next/headers）。
 * Cookie 属性：HttpOnly、SameSite=Strict、Path=/；非 dev 强制 Secure。
 * Max-Age = min(JWT 剩余 TTL, SESSION_MAX_AGE_SECONDS)。
 */

import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { CSRF_COOKIE, SERVER_API_BASE, SESSION_COOKIE, SESSION_MAX_AGE_SECONDS } from "./session";
import type { CurrentUser } from "./server-auth";

/** 读取 JWT payload 的 exp 相对当前时间的剩余秒数；无法解析或缺少 exp 时返回 null。 */
export function jwtRemainingTtlSeconds(token: string): number | null {
  try {
    const parts = token.split(".");
    if (parts.length < 2) return null;
    const payload = JSON.parse(Buffer.from(parts[1], "base64url").toString("utf8"));
    return typeof payload?.exp === "number" ? payload.exp - Math.floor(Date.now() / 1000) : null;
  } catch {
    return null;
  }
}

/** 会话 Max-Age（秒）：不超过 8 小时，且不超过 JWT 剩余 TTL；下限 60 秒。 */
export function sessionMaxAgeSeconds(token: string): number {
  const ttl = jwtRemainingTtlSeconds(token);
  if (ttl === null) return SESSION_MAX_AGE_SECONDS;
  return Math.max(60, Math.min(ttl, SESSION_MAX_AGE_SECONDS));
}

function cookieOptions(maxAge: number, httpOnly: boolean) {
  return {
    httpOnly,
    secure: process.env.NODE_ENV === "production",
    sameSite: "strict" as const,
    path: "/",
    maxAge,
  };
}

/** 创建会话：写入 HttpOnly 会话 cookie + 非敏感 CSRF cookie（随机值）。 */
export async function setSessionCookies(token: string): Promise<void> {
  const store = await cookies();
  const maxAge = sessionMaxAgeSeconds(token);
  store.set(SESSION_COOKIE, token, cookieOptions(maxAge, true));
  store.set(CSRF_COOKIE, crypto.randomUUID(), cookieOptions(maxAge, false));
}

/** 清除会话：同时删除会话与 CSRF cookie。 */
export async function clearSessionCookies(): Promise<void> {
  const store = await cookies();
  store.set(SESSION_COOKIE, "", cookieOptions(0, true));
  store.set(CSRF_COOKIE, "", cookieOptions(0, false));
}

/** 统一错误信封（与后端错误模型一致：{error:{code,message,correlationId,details}}）。 */
export function errorJson(status: number, code: string, message: string): NextResponse {
  return NextResponse.json(
    { error: { code, message, correlationId: null, details: null } },
    { status },
  );
}

/**
 * 联调开关：COMMERCE_SESSION_MOCK=1 且非生产环境时，跳过后端 /v1/me 验证。
 * 仅开发模式使用（本 mock 永不生效于生产）。
 */
export function isSessionMockEnabled(): boolean {
  return process.env.COMMERCE_SESSION_MOCK === "1" && process.env.NODE_ENV !== "production";
}

/** 开发 mock 用户（system_admin，便于联调运维页面）。 */
export function mockUser(): CurrentUser {
  return {
    id: "mock-user",
    username: "mock-user",
    roles: ["system_admin"],
    jwtExpiresAt: null,
  };
}
