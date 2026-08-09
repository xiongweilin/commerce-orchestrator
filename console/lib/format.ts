/** 时间格式化（Asia/Shanghai）。传入非法值时不抛错，原样返回。 */
export function formatTime(iso?: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  try {
    return new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(date);
  } catch {
    return iso;
  }
}

/** 截断长 ID，便于列表展示；title 属性可查看完整值。 */
export function shortId(id: string, max = 8): string {
  if (id.length <= max) return id;
  return `${id.slice(0, max)}…`;
}

/** 任意值转为可展示文本（对象做 JSON 序列化）。 */
export function jsonText(value: unknown): string {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}
