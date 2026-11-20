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
const REQUIRED_CONFIG_KEYS: Record<WfNodeType, readonly string[]> = {
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
export const NODE_ID_RE = /^\w+$/;
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
function defaultPosition(index: number): CanvasPosition {
  return { x: (index % 4) * 240, y: Math.floor(index / 4) * 140 };
}

type Fail = { ok: false; reasons: string[] };
type Ok<T> = { ok: true; value: T };
type Outcome<T> = Ok<T> | Fail;
type ParsedDef = { nodes: Record<string, unknown>[] } | Fail;

/* 顶层只允许 nodes 字段，且必须是数组。 */
function checkDefinitionShape(definition: unknown): ParsedDef {
  if (!isPlainObject(definition)) return { ok: false, reasons: ["定义必须是 JSON 对象"] };
  const unknownTop = Object.keys(definition).filter((k) => k !== "nodes");
  if (unknownTop.length > 0) {
    return { ok: false, reasons: [`定义存在画布不支持的顶层字段: ${unknownTop.join(", ")}`] };
  }
  const rawNodes = definition.nodes;
  if (!Array.isArray(rawNodes)) return { ok: false, reasons: ["definition.nodes 必须是数组"] };
  return { nodes: rawNodes as Record<string, unknown>[] };
}

/* 节点对象只允许 id/type/config/depends_on 四个字段。 */
function checkNodeKeys(raw: Record<string, unknown>, i: number): string | null {
  const unknownKeys = Object.keys(raw).filter(
    (k) => k !== "id" && k !== "type" && k !== "config" && k !== "depends_on",
  );
  return unknownKeys.length > 0
    ? `节点存在画布不支持的字段: ${unknownKeys.join(", ")}(nodes[${i}])`
    : null;
}

/* 节点 id 须为非空、无首尾空白且不重复的字符串。 */
function checkNodeId(
  raw: Record<string, unknown>,
  i: number,
  seenIds: Set<string>,
): Outcome<string> {
  const id = raw.id;
  if (typeof id !== "string" || id.length === 0)
    return { ok: false, reasons: [`nodes[${i}] 缺字符串 id,画布无法表达`] };
  if (id.trim() !== id) return { ok: false, reasons: [`节点 id 含首尾空白,画布无法表达: ${id}`] };
  if (seenIds.has(id)) return { ok: false, reasons: [`节点 id 重复,画布无法表达: ${id}`] };
  return { ok: true, value: id };
}

/* 节点类型须在画布支持的类型集内。 */
function checkNodeType(raw: Record<string, unknown>): WfNodeType | null {
  const type = raw.type;
  return typeof type === "string" && NODE_TYPES.includes(type as WfNodeType)
    ? (type as WfNodeType)
    : null;
}

/* 配置须为对象，键在白名单内（on 除外），on 仅允许字符串 true/false。 */
function checkNodeConfig(
  raw: Record<string, unknown>,
  id: string,
  type: WfNodeType,
): string | null {
  if (!("config" in raw) || !isPlainObject(raw.config))
    return `节点 ${id} 缺 config 对象,画布无法表达`;
  const config = raw.config;
  const whitelist = CONFIG_KEY_WHITELIST[type];
  const unknownConfig = Object.keys(config).filter((k) => k !== "on" && !whitelist.includes(k));
  if (unknownConfig.length > 0)
    return `节点 ${id} 存在画布不支持的字段: ${unknownConfig.join(", ")}`;
  if ("on" in config && config.on !== "true" && config.on !== "false") {
    return `节点 ${id} 的 on 值非法(仅 true/false 字符串),画布无法表达`;
  }
  return null;
}

/* 解析 depends_on：须为字符串数组、无重复、不自依赖。 */
function parseDeps(raw: Record<string, unknown>, id: string): Outcome<string[]> {
  if (!("depends_on" in raw)) return { ok: true, value: [] };
  const rawDeps = raw.depends_on;
  if (!Array.isArray(rawDeps))
    return { ok: false, reasons: [`节点 ${id} 的 depends_on 非数组,画布无法表达`] };
  const deps = rawDeps.filter((d): d is string => typeof d === "string");
  if (deps.length !== rawDeps.length)
    return { ok: false, reasons: [`节点 ${id} 的依赖项非字符串,画布无法表达`] };
  if (new Set(deps).size !== deps.length) {
    return { ok: false, reasons: [`节点 ${id} 存在重复依赖项,画布无法表达`] };
  }
  if (deps.includes(id)) return { ok: false, reasons: [`节点 ${id} 自依赖,画布无法表达`] };
  return { ok: true, value: deps };
}

type DagParseState = {
  nodes: CanvasNode[];
  typeOf: Map<string, WfNodeType>;
  depsById: Map<string, string[]>;
};

/* 解析节点类型,未知类型直接给出带原类型值的拒绝原因。 */
function parseRequiredType(raw: Record<string, unknown>, id: string): Outcome<WfNodeType> {
  const type = checkNodeType(raw);
  if (type === null) {
    return { ok: false, reasons: [`未知节点类型,画布无法表达: ${String(raw.type)}(节点 ${id})`] };
  }
  return { ok: true, value: type };
}

/* 解析节点 id 并登记去重集；返回拒绝原因或字符串 id。 */
function parseNodeId(
  raw: Record<string, unknown>,
  i: number,
  seenIds: Set<string>,
): Outcome<string> {
  const id = checkNodeId(raw, i, seenIds);
  if (id.ok) seenIds.add(id.value);
  return id;
}

/* 检查第 i 个原始节点的对象形态与顶层键；通过返回 null。 */
function checkNodeShape(rawNodes: Record<string, unknown>[], i: number): string | null {
  const raw = rawNodes[i];
  if (!isPlainObject(raw)) return `nodes[${i}] 非对象,画布无法表达`;
  return checkNodeKeys(raw, i);
}

/* 按 id、类型、config、depends_on 解析节点声明；任一检查失败即返回该拒绝原因。 */
function parseNodeDecl(
  raw: Record<string, unknown>,
  id: string,
): Outcome<{ type: WfNodeType; deps: string[] }> {
  const type = parseRequiredType(raw, id);
  if (!type.ok) return type;
  const configError = checkNodeConfig(raw, id, type.value);
  if (configError) return { ok: false, reasons: [configError] };
  const deps = parseDeps(raw, id);
  if (!deps.ok) return deps;
  return { ok: true, value: { type: type.value, deps: deps.value } };
}

/* 解析第 i 个原始节点并返回其声明；任一检查失败即返回该拒绝原因。 */
function parseDagNode(
  rawNodes: Record<string, unknown>[],
  i: number,
  seenIds: Set<string>,
): Outcome<{ id: string; type: WfNodeType; deps: string[] }> {
  const raw = rawNodes[i];
  if (!raw) return { ok: false, reasons: [`nodes[${i}] 缺失,画布无法表达`] };
  const shapeError = checkNodeShape(rawNodes, i);
  if (shapeError) return { ok: false, reasons: [shapeError] };
  const id = parseNodeId(raw, i, seenIds);
  if (!id.ok) return id;
  const decl = parseNodeDecl(raw, id.value);
  if (!decl.ok) return decl;
  return { ok: true, value: { id: id.value, ...decl.value } };
}

/* 逐个解析原始节点，任一处失败即整体拒绝并给出原因。 */
function parseDagNodes(
  rawNodes: Record<string, unknown>[],
  positions?: Record<string, CanvasPosition>,
): DagParseState | Fail {
  const state: DagParseState = { nodes: [], typeOf: new Map(), depsById: new Map() };
  const seenIds = new Set<string>();
  for (let i = 0; i < rawNodes.length; i++) {
    const decl = parseDagNode(rawNodes, i, seenIds);
    if (!decl.ok) return decl;
    const { id, type, deps } = decl.value;
    // parseDagNode 已保证该下标存在且为对象
    state.nodes.push({
      id,
      type,
      config: rawNodes[i]!.config as Record<string, unknown>,
      position: positions?.[id] ?? defaultPosition(state.nodes.length),
      depsExplicit: "depends_on" in rawNodes[i]!,
    });
    state.typeOf.set(id, type);
    state.depsById.set(id, deps);
  }
  return state;
}

/* 依赖引用须指向已声明的节点（全集齐了再查）。 */
function checkDepsExist(state: DagParseState): string | null {
  for (const n of state.nodes) {
    for (const d of state.depsById.get(n.id) ?? []) {
      if (!state.typeOf.has(d)) return `节点 ${n.id} 依赖不存在的节点: ${d}`;
    }
  }
  return null;
}

/* on 的载体检查:有 on 值的节点必须有 ≥1 个条件前驱(画布中 on 画在条件入边上) */
function checkOnCarrier(state: DagParseState): string | null {
  for (const n of state.nodes) {
    if (!("on" in n.config)) continue;
    const hasConditionPred = (state.depsById.get(n.id) ?? []).some(
      (d) => state.typeOf.get(d) === "condition",
    );
    if (!hasConditionPred) return `节点 ${n.id} 的 on 值无条件前驱,画布无法表达`;
  }
  return null;
}

/* 依赖关系展开为画布边；条件前驱的边携带目标节点的 on 值。 */
function buildEdges(state: DagParseState): CanvasEdge[] {
  const edges: CanvasEdge[] = [];
  for (const n of state.nodes) {
    for (const d of state.depsById.get(n.id) ?? []) {
      edges.push({
        id: edgeId(d, n.id),
        source: d,
        target: n.id,
        on: state.typeOf.get(d) === "condition" ? (n.config.on as "true" | "false") : null,
      });
    }
  }
  return edges;
}

/** 将工作流定义转为画布状态；拒绝画布无法无损表示的字段组合，并返回原因。 */
export function dagToCanvas(
  definition: unknown,
  positions?: Record<string, CanvasPosition>,
): DagToCanvasResult {
  const shape = checkDefinitionShape(definition);
  if ("ok" in shape) return shape;
  const state = parseDagNodes(shape.nodes, positions);
  if ("ok" in state) return state;
  const reason = checkDepsExist(state) ?? checkOnCarrier(state);
  if (reason) return { ok: false, reasons: [reason] };
  return { ok: true, graph: { nodes: state.nodes, edges: buildEdges(state) } };
}

/* 按目标节点归拢入边。 */
function groupInEdges(edges: CanvasEdge[]): Map<string, CanvasEdge[]> {
  const inEdges = new Map<string, CanvasEdge[]>();
  for (const e of edges) {
    const list = inEdges.get(e.target) ?? [];
    list.push(e);
    inEdges.set(e.target, list);
  }
  return inEdges;
}

/* 条件入边的 on 值：为空或用值不一视为无法写回；无入边且带 on 则裁剪 on（边是 on 的唯一载体）。 */
function resolveNodeConfig(n: CanvasNode, condIns: CanvasEdge[]): Record<string, unknown> | null {
  const config = { ...n.config };
  if (condIns.length === 0) {
    if ("on" in config) delete config.on;
    return config;
  }
  const values = new Set(condIns.map((e) => e.on));
  if (values.size > 1 || condIns.some((e) => e.on == null)) return null;
  const edge = condIns[0];
  if (!edge) return null;
  config.on = edge.on; // 同目标条件入边同值(节点级单一 on 语义)
  return config;
}

/* 画布节点 → 定义节点；依赖非空或原本显式时保留 depends_on 键。 */
function toDagNode(
  n: CanvasNode,
  inEdges: CanvasEdge[],
  byId: Map<string, CanvasNode>,
): Record<string, unknown> | null {
  const condIns = inEdges.filter((e) => byId.get(e.source)?.type === "condition");
  const config = resolveNodeConfig(n, condIns);
  if (!config) return null;
  const deps = inEdges.map((e) => e.source);
  const node: Record<string, unknown> = { id: n.id, type: n.type, config };
  if (deps.length > 0 || n.depsExplicit) node.depends_on = deps;
  return node;
}

/** 将画布状态转为 DAG 定义。条件入边写入 config.on；同一节点的条件值冲突时拒绝转换，删除全部条件入边时也会删除 on。 */
export function canvasToDag(
  graph: CanvasGraph,
): { ok: true; definition: unknown } | { ok: false; reasons: string[] } {
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const reasons = graph.edges
    .filter((e) => !byId.has(e.source) || !byId.has(e.target))
    .map((e) => `连线端点不存在: ${e.id}`);
  if (reasons.length > 0) return { ok: false, reasons };

  const inEdges = groupInEdges(graph.edges);
  const nodes: Record<string, unknown>[] = [];
  for (const n of graph.nodes) {
    const node = toDagNode(n, inEdges.get(n.id) ?? [], byId);
    if (!node)
      return { ok: false, reasons: [`节点 ${n.id} 存在 on 值不一致的条件入边,无法写回定义`] };
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
