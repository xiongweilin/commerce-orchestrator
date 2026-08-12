#!/usr/bin/env node
/**
 * 从后端 OpenAPI 生成 TypeScript 类型（确定性输出，供 CI `git diff --exit-code` 校验）。
 *
 * 用法：
 *   node scripts/gen-types.mjs
 *
 * 环境变量：
 *   OPENAPI_URL          可选，完整 openapi.json 地址；
 *                        默认取 <COMMERCE_API_BASE|http://127.0.0.1:8000>/openapi.json
 *   COMMERCE_API_BASE    可选，后端地址（仅用于拼默认 openapi.json URL）
 *
 * 输出：console/lib/generated/openapi.ts（自动生成，禁止手改；schema 变更后重新运行本脚本）。
 */

import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const OUT = path.join(ROOT, "lib", "generated", "openapi.ts");

const apiBase = process.env.COMMERCE_API_BASE || "http://127.0.0.1:8000";
const openapiUrl = process.env.OPENAPI_URL || `${apiBase}/openapi.json`;

// ---------------------------------------------------------------------------
// JSON Schema -> TypeScript
// ---------------------------------------------------------------------------

const TS_KEYWORDS = new Set(["type", "interface", "enum", "string", "number", "object", "null"]);

function tsName(name) {
  return TS_KEYWORDS.has(name) ? `${name}Type` : name;
}

function indentBlock(block, pad) {
  return block
    .split("\n")
    .map((line) => (line ? pad + line : line))
    .join("\n");
}

function typeOf(schema, schemas, level = 0) {
  if (!schema || typeof schema !== "object") return "unknown";
  if (typeof schema.$ref === "string") {
    return tsName(schema.$ref.replace("#/components/schemas/", ""));
  }
  if (Array.isArray(schema.allOf)) {
    return schema.allOf.map((s) => typeOf(s, schemas, level)).join(" & ") || "unknown";
  }
  if (Array.isArray(schema.anyOf) || Array.isArray(schema.oneOf)) {
    const parts = (schema.anyOf ?? schema.oneOf).map((s) => typeOf(s, schemas, level));
    if (parts.length === 1) return parts[0];
    const hasNull = parts.includes("null");
    const nonNull = parts.filter((p) => p !== "null");
    if (nonNull.length === 1 && hasNull) return `${nonNull[0]} | null`;
    return parts.join(" | ");
  }
  if (schema.const !== undefined) return JSON.stringify(schema.const);
  if (schema.enum !== undefined) {
    return schema.enum.map((v) => JSON.stringify(v)).join(" | ") || "never";
  }
  switch (schema.type) {
    case "string":
      return "string";
    case "integer":
    case "number":
      return "number";
    case "boolean":
      return "boolean";
    case "null":
      return "null";
    case "array": {
      const item = schema.items ? typeOf(schema.items, schemas, level) : "unknown";
      return `${item}[]`;
    }
    case "object":
      return objectType(schema, schemas, level);
    default:
      return "unknown";
  }
}

function objectType(schema, schemas, level) {
  const props = schema.properties ?? {};
  const required = new Set(Array.isArray(schema.required) ? schema.required : []);
  const keys = Object.keys(props).sort();
  const pad = "  ".repeat(level + 1);
  const lines = ["{"];
  for (const key of keys) {
    const raw = typeOf(props[key], schemas, level + 1);
    const t = raw.includes("\n") ? indentBlock(raw, "  ") : raw;
    const opt = required.has(key) ? "" : "?";
    lines.push(`${pad}${JSON.stringify(key)}${opt}: ${t};`);
  }
  if (schema.additionalProperties === true || schema.additionalProperties === undefined) {
    lines.push(`${pad}[key: string]: unknown;`);
  }
  lines.push(`${"  ".repeat(level)}}`);
  return lines.join("\n");
}

// ---------------------------------------------------------------------------
// 生成
// ---------------------------------------------------------------------------

let spec;
try {
  const res = await fetch(openapiUrl, { cache: "no-store" });
  if (!res.ok) {
    console.error(`获取 OpenAPI 失败：${openapiUrl} -> HTTP ${res.status}`);
    process.exit(1);
  }
  spec = await res.json();
} catch (err) {
  console.error(`获取 OpenAPI 失败：${openapiUrl}（${err instanceof Error ? err.message : err}）`);
  process.exit(1);
}

const schemas = spec?.components?.schemas ?? {};
const specHash = createHash("sha256").update(JSON.stringify(spec)).digest("hex").slice(0, 16);

const names = Object.keys(schemas).sort();
const chunks = [];
for (const name of names) {
  const schema = schemas[name];
  const desc = typeof schema.description === "string" ? schema.description : "";
  const doc = desc ? `/** ${desc} */\n` : "";
  chunks.push(`${doc}export interface ${tsName(name)} ${typeOf(schema, schemas, 0)}`);
}

const output = [
  "// 本文件由 scripts/gen-types.mjs 自动生成，禁止手改。",
  "// 运行：node scripts/gen-types.mjs（或 npm run gen:types）",
  `// 来源：${openapiUrl}`,
  `// OpenAPI spec sha256（前 16 位）：${specHash}`,
  "",
  chunks.join("\n\n"),
  "",
].join("\n");

await mkdir(path.dirname(OUT), { recursive: true });
await writeFile(OUT, output, "utf8");
console.log(`已生成 ${path.relative(ROOT, OUT)}（${names.length} 个 schema，hash=${specHash}）`);
