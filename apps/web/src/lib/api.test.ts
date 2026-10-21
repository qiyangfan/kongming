import { beforeAll, describe, expect, it } from "vitest";

/* 测试 SSE 分帧和错误码映射；导入 api.ts 前需为 Node 环境提供 localStorage 桩。 */

type ApiModule = typeof import("./api");

let apiModule: ApiModule;

beforeAll(async () => {
  const store = new Map<string, string>();
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, String(v)),
      removeItem: (k: string) => void store.delete(k),
      clear: () => store.clear(),
      key: (i: number) => [...store.keys()][i] ?? null,
      get length() {
        return store.size;
      },
    },
  });
  apiModule = await import("./api");
});

describe("dispatchSseBlock 分帧", () => {
  it("合法 stage 帧解析分发,非终态返回 false", () => {
    const stages: string[] = [];
    const ok = apiModule.dispatchSseBlock(
      'event: stage\ndata: {"node":"understand","status":"running","retry_count":0}',
      {
        onStage: (d) => {
          stages.push(`${d.node}:${d.status}`);
        },
      },
    );
    expect(ok).toBe(false);
    expect(stages).toEqual(["understand:running"]);
  });

  it("result/error 终态帧返回 true(流可终止)", () => {
    const ok = apiModule.dispatchSseBlock("event: result\ndata: {}", { onResult: () => {} });
    expect(ok).toBe(true);
    const errOk = apiModule.dispatchSseBlock("event: error\ndata: {}", { onError: () => {} });
    expect(errOk).toBe(true);
  });

  it("CR 已被上层剥除:帧块内残留 CR 仍能解析(逐行兼容)", () => {
    const stages: string[] = [];
    apiModule.dispatchSseBlock(
      'event: stage\r\ndata: {"node":"retrieve","status":"done","retry_count":0}',
      {
        onStage: (d) => {
          stages.push(d.node);
        },
      },
    );
    expect(stages).toEqual(["retrieve"]);
  });

  it("畸形 data JSON 静默跳帧,流不断(返回 false 不回调)", () => {
    const ok = apiModule.dispatchSseBlock("event: stage\ndata: {not json", { onStage: () => {} });
    expect(ok).toBe(false);
  });

  it("心跳注释行/无 event 行/空 data 一律忽略", () => {
    expect(apiModule.dispatchSseBlock(": heartbeat", {})).toBe(false);
    expect(apiModule.dispatchSseBlock('data: {"a":1}', {})).toBe(false);
    expect(apiModule.dispatchSseBlock("event: stage", {})).toBe(false);
  });

  it("未知事件类型忽略(前向兼容),不抛错", () => {
    const ok = apiModule.dispatchSseBlock("event: future_thing\ndata: {}", {});
    expect(ok).toBe(false);
  });

  it("data 多行按换行拼接后 JSON.parse", () => {
    const got: unknown[] = [];
    apiModule.dispatchSseBlock('event: sql\ndata: {\ndata:   "sql": "select 1"\ndata: }', {
      onSql: (d) => got.push(d),
    });
    expect(got).toEqual([{ sql: "select 1" }]);
  });
});

describe("humanizeErrorText 错误码转译", () => {
  it("HTTP 状态码形态转中文", () => {
    expect(apiModule.humanizeErrorText("HTTP 429")).toBe("操作过于频繁,请稍后再试");
    expect(apiModule.humanizeErrorText("HTTP 503")).toBe("服务维护中,请稍后重试");
  });

  it("业务错误码查表转中文;未收录码原样保留", () => {
    expect(apiModule.humanizeErrorText("SESSION_NOT_FOUND")).toBe("该会话不存在或已被删除");
    expect(apiModule.humanizeErrorText("TOTALLY_NEW_CODE")).toBe("TOTALLY_NEW_CODE");
  });

  it("062 成本控制错误码转译(429/503 detail 码)", () => {
    expect(apiModule.humanizeErrorText("CHAT_RATE_LIMITED")).toContain("太频繁");
    expect(apiModule.humanizeErrorText("CHAT_QUOTA_EXCEEDED")).toContain("配额");
  });

  it("中文自然语原样;空值兜底;幂等", () => {
    expect(apiModule.humanizeErrorText("服务开小差了")).toBe("服务开小差了");
    expect(apiModule.humanizeErrorText("")).toBe("请求失败,请稍后重试");
    expect(apiModule.humanizeErrorText(null)).toBe("请求失败,请稍后重试");
    expect(apiModule.humanizeErrorText("HTTP 429")).toBe(apiModule.humanizeErrorText("HTTP 429"));
  });

  it("网络异常文案兜中文", () => {
    expect(apiModule.humanizeErrorText("Failed to fetch")).toBe("网络连接失败,请检查网络后重试");
  });
});
