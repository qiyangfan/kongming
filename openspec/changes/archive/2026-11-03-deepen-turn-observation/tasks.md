# Tasks: deepen-turn-observation

> apply 记录(2026-11-03):api ruff+mypy 全绿(全仓 241 文件),pytest
> 1612 通过(含 0061 迁移应用);web tsc+eslint 全绿;make contracts
> 产物已再生。叠加顺序 ①→②→③ 已遵守:导出列集含评分三列与兜底
> 分类列,明细行含 session_id。

## 1. 迁移(ADR-004)

- [x] 1.1 新迁移 0061:`chat_message` 加 `stage_trace JSONB` nullable
      (有序数组,条目 `{node,status,retry,t}` + 上限 64 截断标记)
      ——down 精确逆

## 2. 轨迹收集与落库

- [x] 2.1 api chat 域轨迹收集器 `chat/stage_trace.py`(纯类,时钟构造
      注入):`_consume_agent_stream` 帧回调内累积 stage 帧(与透传并行,
      消费端相对计时),`_stream_finalize`/`_stream_finalize_unavailable`
      携带;条数上限 64+截断标记、畸形帧跳过、零事件 None(先写失败
      测试 tests/test_stage_trace.py 4 例:成对事件/重试序号/相对耗时
      单调/上限截断/畸形跳过/空流 None)
- [x] 2.2 落库:`_finalize_reply` 与 `_finalize_unavailable`(FAILED
      兜底)经 insert_message 随行写入 stage_trace;同步路径恒 None;
      用户响应/SSE 载荷零携带(轨迹只经 param 传递,不进 ChatOutcome
      响应面——比 design 初稿更收口,行为契约一致)

## 3. 串联查询(ADR-005)

- [x] 3.1 quality 仓储/服务/路由:page 行 SELECT 增 session_id;
      新增 `GET /system/chat-quality/context?message_id=`(锚点 404
      语义=不存在或租户不可见同形;升序最近 50 条轻量行,窗口=锚点
      及此前,锚点恒含;权限点/租户收敛复用既有实现);
      `GET /system/chat-quality/stage-trace/{message_id}` 轨迹详情
      (null=同步轮如实为空;404 同形)
- [x] 3.2 `make contracts` 同步产物入库

## 4. 导出(ADR-005)

- [x] 4.1 `GET /system/chat-quality/export`(筛选参数与 page 同构):
      复用 chat/export.to_csv(BOM/RFC4180/公式注入中和单源)、上限
      5000 行 + 截断标记行(「……已达导出行数上限」如实可辨)、操作
      日志(action=chat-quality.export,detail 含筛选与行数);列集
      =时间/问题/改写问题/状态/兜底分类/重试/用户反馈/AI评分三列/
      trace_id/会话标识/数据源/prompt版本(按已合并变更叠加)
- [x] 4.2 `make contracts` 同步产物入库

## 5. 前端质量台

- [x] 5.1 行操作增「同会话轮次」串联面板(升序列表,锚点轮高亮,
      行内状态/分类/评分徽标)与「执行轨迹」抽屉(有序列表,节点
      中文标签复用 stream-card.STAGE_LABELS 改导出单源;自愈轮次
      标注;同步轮空态如实;截断 warning 提示);工具栏导出按钮
      (api.getBlob 下载通道,导出中态);样式全走语义 token
      (warning/muted/border,无硬编码色)
- [x] 5.2 亮暗两态:复用 DataView/Dialog/StatusBadge 标准件,两态随
      token 自动成立;`pnpm typecheck && pnpm lint` 全绿

## 6. 测试与验收

- [x] 6.1 单测:收集器(test_stage_trace 4 例)/串联(升序+锚点必含/
      中间锚点窗口截断/越权 None 同形/不存在 None)/轨迹读取(流式轮
      有值/同步轮 null)/导出行集(筛选三档/行集字段在位)
      (test_turn_observation 5 例);to_csv 转义/公式中和为共享实现
      (chat 导出既有用例承载,零复制)
- [x] 6.2 手工冒烟:静态与单测层完成;端到端冒烟(真实流式轮→轨迹
      串联导出走查)与亮暗人工走查随交付验收(需本地起服务)
- [x] 6.3 `make check` 对应子集(api ruff+mypy+pytest 1612 通过、
      web tsc+eslint 全绿)已记录;spec scenarios ↔ 测试对照表自查:
      「节点阶段轨迹持久化」(流式落库/同步不落/失败如实/不进用户
      契约)、「同会话轮次串联」(串联升序/上限含锚点/越权 404 同形/
      403=既有权限点用例)、「轮次质量导出」(筛选导出/上限截断标记/
      公式中和=共享 to_csv/403 与操作日志=端点接线)全部覆盖
