# Design: deepen-turn-observation

## Context

stage 事件现态:`streams.stage_wrapper` 在图执行侧对每个节点对发
running/done(含 retry_count),经 custom 通道 → agent SSE → api
`_consume_agent_stream` 逐帧中继给前端,**消费后即弃**,终态落库
(`_finalize_reply`)不含轨迹。质量明细(quality/page)SELECT 列不含
session_id(chat_message 本有该列,纯查询面缺选)。导出先例:
`chat/export.py` 已收敛 UTF-8 BOM/RFC4180/公式注入中和纪律(转义工具
已拆分为共享 `escape_util`),操作日志先例在 chat 导出同款。

## Goals / Non-Goals

- Goal:流式轮获得持久阶段轨迹;管理台能把单轮还原为同会话序列;
  质量数据可批量取出。全部为观测读路径增量,主链路零侵入。
- Non-Goals:见 proposal(ReAct 轨迹、同步轮补录、回放、XLSX、
  可视化画布、用户级切分)。

## Decisions

### D1 轨迹收集点=api 消费回调,agent 零改动

`_consume_agent_stream` 的帧回调已逐帧过手,stage 帧就地累积到本轮
收集器(节点名/状态/retry 由事件自带,相对耗时由消费端计时)——
不新增 agent 事件、不改图。收集器与 persist 的衔接:`ChatOutcome`
携带轨迹,`_finalize_reply` 落库时随行写入;FAILED 兜底落库路径
(传输失败收敛)同样携带已收集部分(如实原则)。同步路径无帧流,
字段恒 null——不虚构。

### D2 存储:一列 JSONB 数组,上限 64 条目 + 截断标记

轨迹与消息 1:1 且查询特征孤立(仅详情抽屉取)——列优于新表(无
JOIN);条目形态 `{node,status,retry,t}`,t=相对轮起点毫秒。
正常轮 ≤16 条目(8 节点×2),上限 64 + `truncated` 标记兜底
(防御性,极端重试风暴不炸行宽)。`stage_trace JSONB NULL`,
down 精确逆(ADR-004;无 CHECK 语义可表,数组形态由应用层收口)。

### D3 串联查询:锚点式轻量端点

`GET /system/chat-quality/context?message_id=<uuid>`:经锚点反查
session_id(404 语义=锚点不存在或租户不可见,复用「越权取他人会话
统一 404」先例),同 session 轮次升序返回最近 50 条轻量行(id/
question/status/fallback_type/score/created_at,字段按已合并变更
为准)。权限点与租户收敛复用 QualityService 既有实现(audit 同款
`_visible_tenant_ids`),零新权限模型。锚点轮必含(截断窗口以锚点
为界回溯,防"锚点不在窗口"的体验断裂)。

### D4 导出:复用 chat 导出纪律,单 CSV 档

`GET /system/chat-quality/export`(查询参数与 page 完全同构):
流式写出,上限 5000 行 + 截断标记行;转义直接调共享 `escape_util`
(BOM/RFC4180/公式中和一次到位,零复制);操作日志 action=
`chat-quality.export`,detail 含筛选条件与行数。列集=明细行字段集,
apply 期按已合并的 add-answer-scoring(评分列)与
add-fallback-taxonomy-clarify(分类列)对齐——三个变更按序叠加,
本变更 tasks 中的列清单以合并后的真实契约为准并在实施时核对
contracts 产物。

### D5 前端:三件都是既有页面的增量

详情抽屉加「执行轨迹」块(有序列表:节点中文名复用 stream-card
既有 STAGE_LABELS 映射,running 中断点如实显示);明细行加
session 短标识,行操作「查看同会话轮次」打开串联面板(复用既有
抽屉形态);工具栏加导出按钮(走既有下载通道)。无新页面、无新
路由,亮暗两态沿用既有 token。

## Risks / Trade-offs

- [轨迹列轻微增大消息行] nullable 且仅流式轮有值,条目上限收口;
  查询面(列表页)不 SELECT 该列,热路径零感知。
- [消费端计时的 t 含中继等待] 轨迹定位是复盘材料非性能计量,
  毫秒级口径误差可接受;精确耗时已由用量时间线承载(LLM 事件级),
  两层不重叠。
- [与 A/B 变更的叠加耦合] 按序 apply + contracts 同步核对(D4);
  三变更各自的 delta 不互相触碰正文,merge 面仅 tasks/contracts
  产物层。

## Migration Plan

一列迁移(nullable JSONB,down 精确逆);存量消息轨迹为空如实;
无开关——收集/落库失败静默(仅日志)即天然降级,不因观测增量给
主链路加开关分支。

## Open Questions

(无——收集点/形态/端点形态/导出纪律均已收口。)
