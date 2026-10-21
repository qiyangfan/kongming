/* 按服务端校验错误文案定位画布节点；未识别的错误不猜测节点，只显示原文。 */

export interface LocatedValidationError {
  /** 定位到的节点 id(可能含不存在的依赖目标;调用方与画布节点求交) */
  nodeIds: string[];
  /** 命中的配置键(缺必填配置/非法 on),供侧栏表单字段级定位 */
  configKey?: string;
  /** 原始文案(展示用,原样透传) */
  message: string;
}

interface LocatePattern {
  re: RegExp;
  nodeIds: (m: RegExpMatchArray) => string[];
  configKey?: (m: RegExpMatchArray) => string;
}

/* 顺序敏感:「节点 id …」前缀形态必须先于通用「节点 <id> …」形态, 否则字面 "id" 会被误捕为节点 id。 */
const PATTERNS: LocatePattern[] = [
  {
    re: /^定义存在环,参与节点: (.+)$/,
    nodeIds: (m) => m[1]!.split(", "),
  },
  {
    re: /^孤立节点\(无依赖且无后继\): (.+)$/,
    nodeIds: (m) => [m[1]!],
  },
  {
    re: /^节点 id 重复: (.+)$/,
    nodeIds: (m) => [m[1]!],
  },
  {
    re: /^节点 id 形态非法\(仅字母\/数字\/下划线\): (.+)$/,
    nodeIds: (m) => [m[1]!],
  },
  {
    re: /^节点 id 撞保留名\/外层节点: (.+)$/,
    nodeIds: (m) => [m[1]!],
  },
  {
    re: /^节点 (.+?)\(.+\)缺必填配置 (.+)$/,
    nodeIds: (m) => [m[1]!],
    configKey: (m) => m[2]!,
  },
  {
    re: /^节点 (.+?) 的 on 仅可为 true\/false$/,
    nodeIds: (m) => [m[1]!],
    configKey: () => "on",
  },
  {
    re: /^节点 (.+?) 依赖不存在的节点: (.+)$/,
    nodeIds: (m) => [m[1]!, m[2]!],
  },
  {
    re: /^节点 (.+?)(?: 类型非法| 自依赖| 存在重复依赖项)/,
    nodeIds: (m) => [m[1]!],
  },
];

export function locateValidationError(message: string): LocatedValidationError {
  for (const p of PATTERNS) {
    const m = message.match(p.re);
    if (m) {
      const ids = p.nodeIds(m).filter((s) => s.length > 0);
      const configKey = p.configKey?.(m);
      return { nodeIds: ids, configKey, message };
    }
  }
  return { nodeIds: [], message };
}
