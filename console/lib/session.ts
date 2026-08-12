/**
 * BFF 会话常量（客户端与服务端共用；不包含任何 Node API）。
 *
 * 会话 JWT 保存在 HttpOnly cookie（JS 不可读）；CSRF 令牌保存在非敏感 cookie，
 * 客户端 JS 读取后随所有非 GET 的 BFF 请求通过 X-CSRF-Token 头回传。
 */

export const SESSION_COOKIE = "commerce_session";
export const CSRF_COOKIE = "commerce_csrf";

/** 会话最大时长上限（秒）：8 小时；实际 Max-Age 取 min(JWT 剩余 TTL, 本上限)。 */
export const SESSION_MAX_AGE_SECONDS = 8 * 60 * 60;

/** 后端地址：服务器私有环境变量（仅 BFF / 服务端组件使用；客户端只访问同源 BFF）。 */
export const SERVER_API_BASE: string =
  typeof process !== "undefined" && process.env && typeof process.env.COMMERCE_API_BASE === "string"
    ? process.env.COMMERCE_API_BASE
    : "http://localhost:8000";
