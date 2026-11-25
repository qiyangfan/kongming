// 从 contracts/openapi.json 的 x-sse-data-schema 生成聊天流事件的独立校验模块。
// 产物自包含(浏览器无需加载 Ajv 运行时),确定性输出(重复生成零差异)。
// 用法:node scripts/gen-sse-validator.mjs(contracts:gen 的最后一环)
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

import Ajv2020 from "ajv/dist/2020.js";
import standaloneCode from "ajv/dist/standalone/index.js";
import { format, resolveConfig } from "prettier";

const here = path.dirname(fileURLToPath(import.meta.url));
const specPath = path.resolve(here, "../../../contracts/openapi.json");
const outJs = path.resolve(here, "../src/lib/chat-stream-validate.gen.js");
const outDts = path.resolve(here, "../src/lib/chat-stream-validate.gen.d.ts");

const spec = JSON.parse(readFileSync(specPath, "utf8"));
const op = spec.paths["/chat/query/stream"].post;
const sse = op.responses["200"].content["text/event-stream"];
const dataSchema = sse["x-sse-data-schema"];
if (!dataSchema) {
  throw new Error("契约缺 x-sse-data-schema:先跑 api 的 export_openapi.py");
}

const ajv = new Ajv2020({
  strict: true,
  allErrors: false,
  // 事件对象一律禁未知字段(契约模型已 forbid),校验器同样拒绝
  code: { source: true, esm: true },
});

// components 注入 $defs 供 oneOf 分支的 $ref 解析;
// $ref 前缀从 OpenAPI 布局(#/components/schemas/)改写到 #/$defs/;
// discriminator 是 OpenAPI 扩展关键字而非 JSON Schema,交给 Ajv 前剥除
// (分支互斥由各组件 type 常量表达)
const { discriminator: _discard, ...branchSchema } = JSON.parse(JSON.stringify(dataSchema));
const rewriteRefs = (node) => {
  if (Array.isArray(node)) {
    node.forEach(rewriteRefs);
    return;
  }
  if (node && typeof node === "object") {
    for (const [k, v] of Object.entries(node)) {
      if (k === "$ref" && typeof v === "string") {
        node[k] = v.replace("#/components/schemas/", "#/$defs/");
      } else {
        rewriteRefs(v);
      }
    }
  }
};
rewriteRefs(branchSchema);
const defs = JSON.parse(JSON.stringify(spec.components.schemas));
rewriteRefs(defs);
const schema = {
  $schema: "https://json-schema.org/draft/2020-12/schema",
  ...branchSchema,
  $defs: defs,
};

const validate = ajv.compile(schema);
const mod = standaloneCode(ajv, validate);
// 产物直接进 format:check 门禁,生成时即按仓库 prettier 配置定型(否则每次再生成都要手跑格式化)
const prettierConfig = (await resolveConfig(outJs)) ?? {};
const modFormatted = await format(`${mod}\n`, { ...prettierConfig, parser: "babel" });
writeFileSync(outJs, modFormatted, "utf8");

writeFileSync(
  outDts,
  `// 生成产物:聊天流公开事件的运行时校验(勿手改;contracts:gen 再生成)
// 返回 true 表示收齐的 data: JSON 是六种公开事件之一且字段形状合格
declare function validateChatStreamEvent(value: unknown): value is { type: string; data: unknown };

export default validateChatStreamEvent;
`,
  "utf8",
);
console.log(`sse validator → ${path.relative(process.cwd(), outJs)}`);
