import { createElement } from "react";
import type { ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

/* mock 掉 rag-api 值导入:切断 api.ts → tenant-store(模块顶层 localStorage) 的加载链,node 环境零 jsdom 下的纯静态渲染守卫不触真实客户端 */
vi.mock("@kongming/api-client/rag-api", () => ({ ragApi: { probe: () => undefined } }));

import { ProbeResult, RecallProbePanel } from "@kongming/ui/recall-probe-panel";
import type { RecallProbeOut } from "@kongming/api-client/rag-api";

/* 使用服务端渲染验证召回试跑的命中、裁剪、旁路、扩展结果和空态。 */

const FULL: RecallProbeOut = {
  question: "查询库存数量",
  gate_skipped: null,
  expansion: {
    enabled: true,
    tables: [
      {
        schema_name: "public",
        table_name: "customers",
        via: {
          source_table: "public.orders",
          target_table: "public.customers",
          join_keys: [{ left_col: "customer_id", right_col: "id" }],
          source: "manual",
        },
      },
    ],
    fused_after: [
      { schema_name: "public", table_name: "orders" },
      { schema_name: "public", table_name: "customers" },
    ],
  },
  vector: {
    hits: [{ schema_name: "public", table_name: "orders", score: 0.87, source: "vector" }],
    verdict: { passed: true, reason: "passed", top1: 0.87, mean_top_n: 0.87, count: 1 },
    dropped: 0,
  },
  fulltext: {
    hits: [],
    verdict: { passed: false, reason: "no_hits", top1: 0, mean_top_n: 0, count: 0 },
    dropped: 0,
  },
  fused: [{ schema_name: "public", table_name: "orders", score: 0.016393 }],
};

const render = (element: ReactElement): string => renderToStaticMarkup(element);

const renderResult = (out: RecallProbeOut): string => render(createElement(ProbeResult, { out }));

describe("RecallProbePanel 试跑结果渲染", () => {
  it("完整形态:过闸徽章/命中表/经由关系/扩展后集合如实透出", () => {
    const html = renderResult(FULL);
    expect(html).toContain("过闸");
    expect(html).toContain("public.orders");
    expect(html).toContain("public.customers");
    expect(html).toContain("经由 public.orders → public.customers");
    expect(html).toContain("customer_id = id");
    expect(html).toContain("扩展后表集合");
    expect(html).toContain("0.87"); // 原生分观测
    expect(html).toContain("零命中(该路不参与判定)"); // 全文路零命中不静默
  });

  it("整路被裁形态:未过闸归因与被裁命中数如实", () => {
    const dropped: RecallProbeOut = {
      ...FULL,
      vector: {
        hits: [],
        verdict: { passed: false, reason: "mean_below", top1: 0.1, mean_top_n: 0.12, count: 3 },
        dropped: 3,
      },
    };
    const html = renderResult(dropped);
    expect(html).toContain("未过闸·分值低于阈值");
    expect(html).toContain("被裁 3 条");
  });

  it("零知识旁路形态:标注「闸门未参与(无知识)」,不呈现为过闸", () => {
    const bypass: RecallProbeOut = {
      ...FULL,
      gate_skipped: "no_knowledge",
      vector: { hits: [], verdict: null, dropped: 0 },
      fulltext: { hits: [], verdict: null, dropped: 0 },
    };
    const html = renderResult(bypass);
    expect(html).toContain("闸门未参与(无知识)");
    expect(html).toContain("闸门未参与判定");
    expect(html).not.toContain("未过闸");
    expect(html).not.toContain(">过闸<");
  });

  it("扩展未开启形态:徽章如实为「未开启」,不渲染扩展明细", () => {
    const off: RecallProbeOut = {
      ...FULL,
      expansion: { enabled: false, tables: [], fused_after: [] },
    };
    const html = renderResult(off);
    expect(html).toContain("未开启");
    expect(html).toContain("召回结果不含关系补入表");
    expect(html).not.toContain("扩展后表集合");
  });

  it("开启零扩展形态:明细区如实标注零扩展,仍透出扩展后表集合", () => {
    const empty: RecallProbeOut = {
      ...FULL,
      expansion: {
        enabled: true,
        tables: [],
        fused_after: [{ schema_name: "public", table_name: "orders" }],
      },
    };
    const html = renderResult(empty);
    expect(html).toContain("本轮零扩展");
    expect(html).toContain("扩展后表集合");
    expect(html).not.toContain("经由");
  });
});

describe("RecallProbePanel 面板骨架", () => {
  it("未试跑时空态引导,试跑中禁重复提交", () => {
    const html = render(
      createElement(
        QueryClientProvider,
        { client: new QueryClient() },
        createElement(RecallProbePanel, { options: [] }),
      ),
    );
    expect(html).toContain("召回试跑");
    expect(html).toContain("输入问句并选择数据源后点「试跑」");
    expect(html).toContain("disabled"); // 问句为空时试跑按钮禁用
  });
});
