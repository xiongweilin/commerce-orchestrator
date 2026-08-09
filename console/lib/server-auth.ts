import { cookies } from "next/headers";
import { TOKEN_COOKIE } from "./api";

/**
 * 服务端读取 token（仅供服务端组件使用）：
 * 客户端 TokenInput 会把 localStorage 中的 commerce_token 同步到同名 cookie，
 * 因此服务端组件在页面请求时也能携带该 token 调用后端。
 */
export async function getServerToken(): Promise<string | null> {
  const store = await cookies();
  return store.get(TOKEN_COOKIE)?.value ?? null;
}
