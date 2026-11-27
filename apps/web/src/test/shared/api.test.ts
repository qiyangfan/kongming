import { beforeAll, describe, expect, it, vi } from "vitest";

/* 测试 SSE 分帧和错误码映射；导入 api.ts 前需为 Node 环境提供 localStorage 桩。 */

type ApiModule = typeof import("@kongming/api-client/api");

let apiModule: ApiModule;

beforeAll(async () => {
  const store = new Map<string, string>();
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => {
        store.set(k, String(v));
      },
      removeItem: (k: string) => {
        store.delete(k);
      },
      clear: () => store.clear(),
      key: (i: number) => [...store.keys()][i] ?? null,
      get length() {
        return store.size;
      },
    },
  });
  apiModule = await import("@kongming/api-client/api");
});

describe("dispatchSseBlock 分帧与分发", () => {
  it("合法 stage 帧按 type 分发,非终态返回 false", () => {
    const stages: string[] = [];
    const ok = apiModule.dispatchSseBlock(
      'data: {"type":"stage","data":{"node":"understand","status":"running","retry_count":0}}',
      {
        onStage: (d) => {
          stages.push(`${d.node}:${d.status}`);
        },
      },
    );
    expect(ok).toBe(false);
    expect(stages).toEqual(["understand:running"]);
  });

  it("合法 result 终态帧返回 true;data 与同步响应同构", () => {
    const got: unknown[] = [];
    const data = {
      session_id: null,
      success: true,
      verdict: "PASS",
      summary: "共 1 行",
      insights: [],
      stats: {},
      columns: [],
      rows: [],
      sql: "select 1",
      chart: { type: "table" },
      retry_count: 0,
      error: null,
    };
    const ok = apiModule.dispatchSseBlock(`data: ${JSON.stringify({ type: "result", data })}`, {
      onResult: (d) => got.push(d),
    });
    expect(ok).toBe(true);
    expect(got).toEqual([data]);
  });

  it("空对象不能冒充结果:无效 result 跳过且不终止", () => {
    const results: unknown[] = [];
    const ok = apiModule.dispatchSseBlock('data: {"type":"result","data":{}}', {
      onResult: (d) => results.push(d),
    });
    expect(ok).toBe(false);
    expect(results).toEqual([]);
  });

  it("错误字段缺失或 reason_code 不在枚举内:跳帧", () => {
    const errs: unknown[] = [];
    for (const data of [{ error: "失败" }, { error: "失败", reason_code: "SOMETHING_ELSE" }]) {
      const ok = apiModule.dispatchSseBlock(`data: ${JSON.stringify({ type: "error", data })}`, {
        onError: (d) => errs.push(d),
      });
      expect(ok).toBe(false);
    }
    expect(errs).toEqual([]);
  });

  it("CR 已被上层剥除:帧块内残留 CR 仍能解析(逐行兼容)", () => {
    const stages: string[] = [];
    apiModule.dispatchSseBlock(
      'data: {"type":"stage","data":{"node":"retrieve","status":"done","retry_count":0}}\r\n',
      {
        onStage: (d) => {
          stages.push(d.node);
        },
      },
    );
    expect(stages).toEqual(["retrieve"]);
  });

  it("畸形 data JSON 静默跳帧,流不断(返回 false 不回调)", () => {
    const ok = apiModule.dispatchSseBlock("data: {not json", { onStage: () => {} });
    expect(ok).toBe(false);
  });

  it("心跳注释行/无 data 行一律忽略", () => {
    expect(apiModule.dispatchSseBlock(": ping", {})).toBe(false);
    expect(apiModule.dispatchSseBlock("", {})).toBe(false);
  });

  it("未知事件类型或内部诊断 trace 跳过(校验器拒绝),不抛错", () => {
    const ok1 = apiModule.dispatchSseBlock('data: {"type":"future_thing","data":{}}', {});
    const ok2 = apiModule.dispatchSseBlock(
      'data: {"type":"trace","data":{"node":"n","fields":{}}}',
      {},
    );
    expect(ok1).toBe(false);
    expect(ok2).toBe(false);
  });

  it("data 多行按换行拼接后 JSON.parse", () => {
    const got: unknown[] = [];
    apiModule.dispatchSseBlock(
      'data: {\ndata:   "type": "sql",\ndata:   "data": {"sql": "select 1"}\ndata: }',
      {
        onSql: (d) => got.push(d),
      },
    );
    expect(got).toEqual([{ sql: "select 1" }]);
  });

  it("类型与数据错配跳帧(stage 带 sql/字符串重试次数)", () => {
    const stages: unknown[] = [];
    const bad1 = apiModule.dispatchSseBlock('data: {"type":"stage","data":{"sql":"select 1"}}', {
      onStage: (d) => stages.push(d),
    });
    const bad2 = apiModule.dispatchSseBlock(
      'data: {"type":"stage","data":{"node":"g","status":"running","retry_count":"1"}}',
      { onStage: (d) => stages.push(d) },
    );
    expect(bad1).toBe(false);
    expect(bad2).toBe(false);
    expect(stages).toEqual([]);
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

const stageFrame = (node: string) =>
  `data: ${JSON.stringify({ type: "stage", data: { node, status: "running", retry_count: 0 } })}\n\n`;

describe("drainSseBuffer 分块边界与结束语义", () => {
  it("中文字符跨网络分块截断:按 SSE 规则恢复完整消息", () => {
    const got: string[] = [];
    const handlers = { onSummary: (d: { delta: string }) => got.push(d.delta) };
    // "销售额"在 chunk 边界被切开
    const { buffer: b1, terminated: t1 } = apiModule.drainSseBuffer(
      'data: {"type":"summary","data":{"delta":"销售',
      handlers,
    );
    expect(t1).toBe(false);
    expect(got).toEqual([]);
    const { buffer: b2, terminated: t2 } = apiModule.drainSseBuffer(b1 + '额"}}\n\n', handlers);
    expect(t2).toBe(false);
    expect(got).toEqual(["销售额"]);
    expect(b2).toBe("");
  });

  it("CRLF 分隔经读取层剥 CR 后与 LF 等价(两帧都分发)", async () => {
    const frame = `data: ${JSON.stringify({ type: "stage", data: { node: "a", status: "running", retry_count: 0 } })}\r\n\r\n`;
    const chunks = [frame, frame];
    let i = 0;
    const reader = {
      read: () => {
        if (i < chunks.length) return { done: false, value: new TextEncoder().encode(chunks[i++]) };
        return Promise.resolve({ done: true, value: undefined });
      },
    } as unknown as ReadableStreamDefaultReader<Uint8Array>;
    const nodes: string[] = [];
    await apiModule.readSseStream(reader, { onStage: (d) => nodes.push(d.node) });
    expect(nodes).toEqual(["a", "a"]); // 两帧都完整恢复
  });

  it("一个分块含合法结尾与后续消息:只分发到第一条合法结尾", () => {
    const events: string[] = [];
    const resultData = {
      session_id: null,
      success: true,
      verdict: "PASS",
      summary: "s",
      insights: [],
      stats: {},
      columns: [],
      rows: [],
      sql: "",
      chart: { type: "table" },
      retry_count: 0,
      error: null,
    };
    const block =
      stageFrame("understand") +
      `data: ${JSON.stringify({ type: "result", data: resultData })}\n\n` +
      stageFrame("late") +
      `data: ${JSON.stringify({ type: "summary", data: { delta: "迟到" } })}\n\n`;
    const { buffer, terminated } = apiModule.drainSseBuffer(block, {
      onStage: () => events.push("stage"),
      onSummary: () => events.push("summary"),
      onResult: () => events.push("result"),
    });
    expect(terminated).toBe(true);
    expect(events).toEqual(["stage", "result"]); // 迟到的 stage/summary 不分发
    expect(buffer).not.toBe(""); // 同块剩余字节留在缓冲,不再消费(上层据此停止读取)
  });

  it("末尾缺少空行:完整帧不早发,留在缓冲等后续字节", () => {
    const nodes: string[] = [];
    const { buffer, terminated } = apiModule.drainSseBuffer(stageFrame("a").trimEnd(), {
      onStage: (d) => nodes.push(d.node),
    });
    expect(nodes).toEqual([]);
    expect(terminated).toBe(false);
    expect(buffer).toContain("node");
  });

  it("消息之间的心跳注释不触发分发", () => {
    const nodes: string[] = [];
    const { terminated } = apiModule.drainSseBuffer(`: ping\n\n${stageFrame("a")}`, {
      onStage: (d) => nodes.push(d.node),
    });
    expect(nodes).toEqual(["a"]);
    expect(terminated).toBe(false);
  });
});

describe("readSseStream 读取结束", () => {
  const resultData = {
    session_id: null,
    success: true,
    verdict: "PASS",
    summary: "s",
    insights: [],
    stats: {},
    columns: [],
    rows: [],
    sql: "",
    chart: { type: "table" },
    retry_count: 0,
    error: null,
  };
  const resultFrame = `data: ${JSON.stringify({ type: "result", data: resultData })}\n\n`;

  it("读到合法结尾即停止读取(不再消费 reader 后续块)", () => {
    let readCalls = 0;
    const chunks = [resultFrame, `data: {"type":"stage","data":{}}\n\n`];
    const reader = {
      read: () => {
        readCalls += 1;
        return Promise.resolve({
          done: false,
          value: new TextEncoder().encode(chunks[readCalls - 1] ?? ""),
        });
      },
    } as unknown as ReadableStreamDefaultReader<Uint8Array>;
    const got: string[] = [];
    const terminated = apiModule.readSseStream(reader, { onResult: () => got.push("result") });
    return terminated.then((t) => {
      expect(t).toBe(true);
      expect(readCalls).toBe(1); // 结尾之后的块不再读取
      expect(got).toEqual(["result"]);
    });
  });

  it("读完仍无合法结尾:返回 false(上层报答案未完整返回)", async () => {
    const chunks = [stageFrame("a"), stageFrame("b")];
    let i = 0;
    const reader = {
      read: () => {
        if (i < chunks.length) return { done: false, value: new TextEncoder().encode(chunks[i++]) };
        return Promise.resolve({ done: true, value: undefined });
      },
    } as unknown as ReadableStreamDefaultReader<Uint8Array>;
    expect(await apiModule.readSseStream(reader, {})).toBe(false);
  });
});

describe("openVoiceChannel 子协议票据", () => {
  it("URL 不含票据,票据由 WebSocket 子协议传递", () => {
    const seen: Array<{ url: string; protocols?: string | string[] | undefined }> = [];

    class FakeWS {
      onmessage: ((ev: MessageEvent) => void) | null = null;
      onclose: (() => void) | null = null;
      constructor(
        public url: string,
        public protocols?: string | string[],
      ) {
        seen.push(this);
      }
    }

    vi.stubGlobal("window", { location: { origin: "https://km.example" } });
    vi.stubGlobal("WebSocket", FakeWS);
    try {
      apiModule.openVoiceChannel(
        "tk-1",
        () => {},
        () => {},
      );
      expect(seen).toHaveLength(1);
      expect(seen[0]!.url).toBe("wss://km.example/api/voice/calls/channel");
      expect(seen[0]!.protocols).toEqual(["tk-1"]);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
