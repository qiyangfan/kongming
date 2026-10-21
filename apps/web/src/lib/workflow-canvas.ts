/* 在工作流定义与画布状态间无损转换；画布可表达性按后端模型判断，结构校验仍由服务端负责。 */

export const WF_NODE_MIME = "application/x-km-wf-node";

export const NODE_TYPES = ["sql", "http", "condition", "loop", "transform", "agent"] as const;
export type WfNodeType = (typeof NODE_TYPES)[number];

export const NODE_TYPE_LABELS: Record<WfNodeType, string> = {
  sql: "SQL 查询",
  http: "HTTP 调用",
  condition: "条件分支",
  loop: "循环",
  transform: "模板转换",
  agent: "AI 步骤",
};

/** 类型必填配置键(与 models.py _REQUIRED_CONFIG 逐键对齐,presence 语义) */
export const REQUIRED_CONFIG_KEYS: Record<WfNodeType, readonly string[]> = {
  sql: ["sql"],
  http: ["url", "method"],
  condition: ["left", "op", "right"],
  loop: ["items_ref", "body", "max_iterations"],
  transform: ["template"],
  agent: ["instruction"],
};

/** 画布支持的配置键；白名单外字段会拒绝转换，避免保存时丢字段。loop.body 由服务端校验。 */
const CONFIG_KEY_WHITELIST: Record<WfNodeType, readonly string[]> = {
  sql: [...REQUIRED_CONFIG_KEYS.sql, "datasource_key"],
  http: [...REQUIRED_CONFIG_KEYS.http, "headers", "body"],
  condition: REQUIRED_CONFIG_KEYS.condition,
  loop: REQUIRED_CONFIG_KEYS.loop,
  transform: REQUIRED_CONFIG_KEYS.transform,
  agent: [...REQUIRED_CONFIG_KEYS.agent, "datasource_key"],
};

/** 节点 id 形态与保留名(与 models.py _NODE_ID_RE/_RESERVED_IDS 对齐; 新建节点生成 id 时消费,录入侧仍由服务端校验兜底) */
export const NODE_ID_RE = /^[A-Za-z0-9_]+$/;
export const RESERVED_NODE_IDS: ReadonlySet<string> = new Set(["inputs", "_loop"]);

export interface CanvasPosition {
  x: number;
  y: number;
}

export interface CanvasNode {
  id: string;
  type: WfNodeType;
  config: Record<string, unknown>;
  position: CanvasPosition;
  /** 原定义是否显式携带 depends_on 键(无依赖时序列化保真:显式键保留空数组) */
  depsExplicit: boolean;
}

export interface CanvasEdge {
  id: string;
  source: string; // 依赖前驱(被 depends_on 指向的节点)
  target: string; // 后继(depends_on 所属节点)
  /** 仅 source 为 condition 的入边携带(即 target.config.on);其余恒 null */
  on: "true" | "false" | null;
}

export interface CanvasGraph {
  nodes: CanvasNode[];
  edges: CanvasEdge[];
}

export type DagToCanvasResult = { ok: true; graph: CanvasGraph } | { ok: false; reasons: string[] };

export function edgeId(source: string, target: string): string {
  return `${source}->${target}`;
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** 网格缺省布局(确定性纯函数;坐标只是画布私有态,不进定义契约) */
export function defaultPosition(index: number): CanvasPosition {
  return { x: (index % 4) * 240, y: Math.floor(index / 4) * 140 };
}

/** 将工作流定义转为画布状态；拒绝画布无法无损表示的字段组合，并返回原因。 */
export function dagToCanvas(
  definition: unknown,
  positions?: Record<string, CanvasPosition>,
): DagToCanvasResult {
  if (!isPlainObject(definition)) return { ok: false, reasons: ["定义必须是 JSON 对象"] };
  const unknownTop = Object.keys(definition).filter((k) => k !== "nodes");
  if (unknownTop.length > 0)
    return {
      ok: false,
      reasons: [`定义存在画布不支持的顶层字段: ${unknownTop.join(", ")}`],
    };
  const rawNodes = definition.nodes;
  if (!Array.isArray(rawNodes)) return { ok: false, reasons: ["definition.nodes 必须是数组"] };

  const nodes: CanvasNode[] = [];
  const typeOf = new Map<string, WfNodeType>();
  const depsById = new Map<string, string[]>();
  const seenIds = new Set<string>();
  for (let i = 0; i < rawNodes.length; i++) {
    const raw = rawNodes[i];
    if (!isPlainObject(raw)) return { ok: false, reasons: [`nodes[${i}] 非对象,画布无法表达`] };
    const unknownKeys = Object.keys(raw).filter(
      (k) => k !== "id" && k !== "type" && k !== "config" && k !== "depends_on",
    );
    if (unknownKeys.length > 0)
      return {
        ok: false,
        reasons: [`节点存在画布不支持的字段: ${unknownKeys.join(", ")}(nodes[${i}])`],
      };
    const id = raw.id;
    if (typeof id !== "string" || id.length === 0)
      return { ok: false, reasons: [`nodes[${i}] 缺字符串 id,画布无法表达`] };
    if (id.trim() !== id) return { ok: false, reasons: [`节点 id 含首尾空白,画布无法表达: ${id}`] };
    if (seenIds.has(id)) return { ok: false, reasons: [`节点 id 重复,画布无法表达: ${id}`] };
    seenIds.add(id);
    const type = raw.type;
    if (typeof type !== "string" || !NODE_TYPES.includes(type as WfNodeType))
      return {
        ok: false,
        reasons: [`未知节点类型,画布无法表达: ${String(type)}(节点 ${id})`],
      };
    if (!("config" in raw) || !isPlainObject(raw.config))
      return { ok: false, reasons: [`节点 ${id} 缺 config 对象,画布无法表达`] };
    const config = raw.config;
    const whitelist = CONFIG_KEY_WHITELIST[type as WfNodeType];
    const unknownConfig = Object.keys(config).filter((k) => k !== "on" && !whitelist.includes(k));
    if (unknownConfig.length > 0)
      return {
        ok: false,
        reasons: [`节点 ${id} 存在画布不支持的字段: ${unknownConfig.join(", ")}`],
      };
    if ("on" in config && config.on !== "true" && config.on !== "false")
      return {
        ok: false,
        reasons: [`节点 ${id} 的 on 值非法(仅 true/false 字符串),画布无法表达`],
      };

    let deps: string[] = [];
    if ("depends_on" in raw) {
      const rawDeps = raw.depends_on;
      if (!Array.isArray(rawDeps))
        return { ok: false, reasons: [`节点 ${id} 的 depends_on 非数组,画布无法表达`] };
      for (const d of rawDeps)
        if (typeof d !== "string")
          return { ok: false, reasons: [`节点 ${id} 的依赖项非字符串,画布无法表达`] };
      if (new Set(rawDeps).size !== rawDeps.length)
        return { ok: false, reasons: [`节点 ${id} 存在重复依赖项,画布无法表达`] };
      if (rawDeps.includes(id)) return { ok: false, reasons: [`节点 ${id} 自依赖,画布无法表达`] };
      deps = rawDeps;
    }
    nodes.push({
      id,
      type: type as WfNodeType,
      config,
      position: positions?.[id] ?? defaultPosition(nodes.length),
      depsExplicit: "depends_on" in raw,
    });
    typeOf.set(id, type as WfNodeType);
    depsById.set(id, deps);
  }
  // 依赖引用存在性(第二遍:全集齐了再查)
  for (const n of nodes) {
    for (const d of depsById.get(n.id) ?? []) {
      if (!typeOf.has(d)) return { ok: false, reasons: [`节点 ${n.id} 依赖不存在的节点: ${d}`] };
    }
  }
  // on 的载体检查:有 on 值的节点必须有 ≥1 个条件前驱(画布中 on 画在条件入边上)
  for (const n of nodes) {
    if ("on" in n.config) {
      const hasConditionPred = (depsById.get(n.id) ?? []).some(
        (d) => typeOf.get(d) === "condition",
      );
      if (!hasConditionPred)
        return {
          ok: false,
          reasons: [`节点 ${n.id} 的 on 值无条件前驱,画布无法表达`],
        };
    }
  }

  const edges: CanvasEdge[] = [];
  for (const n of nodes) {
    for (const d of depsById.get(n.id) ?? []) {
      edges.push({
        id: edgeId(d, n.id),
        source: d,
        target: n.id,
        on: typeOf.get(d) === "condition" ? (n.config.on as "true" | "false") : null,
      });
    }
  }
  return { ok: true, graph: { nodes, edges } };
}

/** 将画布状态转为 DAG 定义。条件入边写入 config.on；同一节点的条件值冲突时拒绝转换，删除全部条件入边时也会删除 on。 */
export function canvasToDag(
  graph: CanvasGraph,
): { ok: true; definition: unknown } | { ok: false; reasons: string[] } {
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const reasons: string[] = [];
  for (const e of graph.edges) {
    if (!byId.has(e.source) || !byId.has(e.target)) reasons.push(`连线端点不存在: ${e.id}`);
  }
  if (reasons.length > 0) return { ok: false, reasons };

  const inEdges = new Map<string, CanvasEdge[]>();
  for (const e of graph.edges) {
    const list = inEdges.get(e.target) ?? [];
    list.push(e);
    inEdges.set(e.target, list);
  }

  const nodes: Record<string, unknown>[] = [];
  for (const n of graph.nodes) {
    const deps = (inEdges.get(n.id) ?? []).map((e) => e.source);
    const condIns = (inEdges.get(n.id) ?? []).filter(
      (e) => byId.get(e.source)?.type === "condition",
    );
    const config = { ...n.config };
    if (condIns.length > 0) {
      const values = new Set(condIns.map((e) => e.on));
      if (values.size > 1 || condIns.some((e) => e.on == null))
        return {
          ok: false,
          reasons: [`节点 ${n.id} 存在 on 值不一致的条件入边,无法写回定义`],
        };
      config.on = condIns[0]!.on; // 同目标条件入边同值(节点级单一 on 语义)
    } else if ("on" in config) {
      // 条件入边全没了 → 裁剪 on(边是 on 的唯一载体,删边即删 on)
      delete config.on;
    }
    const node: Record<string, unknown> = { id: n.id, type: n.type, config };
    if (deps.length > 0 || n.depsExplicit) node.depends_on = deps;
    nodes.push(node);
  }
  return { ok: true, definition: { nodes } };
}

/** 新建节点 id 生成(类型名打底,碰撞加序;形态与保留名规则对齐服务端) */
export function nextNodeId(taken: readonly { id: string }[], type: WfNodeType): string {
  const used = new Set(taken.map((n) => n.id));
  if (!used.has(type)) return type;
  for (let i = 2; ; i++) {
    const candidate = `${type}_${i}`;
    if (!used.has(candidate)) return candidate;
  }
}

/** 节点缺必填配置键(presence 语义,与服务端逐键对齐;仅作 UX 提示,不拦截保存) */
export function missingRequiredKeys(type: WfNodeType, config: Record<string, unknown>): string[] {
  return REQUIRED_CONFIG_KEYS[type].filter((k) => !(k in config));
}

/* 布局坐标按工作流和版本保存在本地;它属于个人偏好,不会跨设备迁移。 */

const POS_KEY_PREFIX = "km.wfcanvas.pos";

function positionsKey(wfKey: string, version: number): string {
  return `${POS_KEY_PREFIX}.${wfKey || "_"}.v${version}`;
}

export function loadCanvasPositions(
  wfKey: string,
  version: number,
): Record<string, CanvasPosition> {
  try {
    const raw = localStorage.getItem(positionsKey(wfKey, version));
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (!isPlainObject(parsed)) return {};
    const out: Record<string, CanvasPosition> = {};
    for (const [id, pos] of Object.entries(parsed)) {
      if (isPlainObject(pos) && typeof pos.x === "number" && typeof pos.y === "number")
        out[id] = { x: pos.x, y: pos.y };
    }
    return out;
  } catch {
    return {};
  }
}

export function saveCanvasPositions(
  wfKey: string,
  version: number,
  positions: Record<string, CanvasPosition>,
): void {
  try {
    localStorage.setItem(positionsKey(wfKey, version), JSON.stringify(positions));
  } catch {
    /* 配额满/隐私模式:布局持久化失败可忽略(下次打开回落网格) */
  }
}

/** 保存成功后把布局从旧版本键迁到新版本键(同 key 新版本=新 localStorage 键) */
export function moveCanvasPositions(wfKey: string, fromVersion: number, toVersion: number): void {
  if (fromVersion === toVersion) return;
  const positions = loadCanvasPositions(wfKey, fromVersion);
  if (Object.keys(positions).length === 0) return;
  saveCanvasPositions(wfKey, toVersion, positions);
  try {
    localStorage.removeItem(positionsKey(wfKey, fromVersion));
  } catch {
    /* ignore */
  }
}
