# 022 agent-memory 实施任务

## 1. Schema

- [x] 1.1 alembic 0011:user_memory(design D1;HNSW 部分索引 dormant=false)

## 2. memory 域包(测试先行:双维隔离/PII 拒绝)

- [x] 2.1 失败测试:召回双过滤(跨用户/跨租户不可见)+ dormant 排除 + 命中刷新
- [x] 2.2 失败测试:显式形成(意图词提取/PII 拒落/确认答复短路不走 agent)
- [x] 2.3 失败测试:自管理四端点(列表/编辑唤醒/删除/导出/越权 404)
- [x] 2.4 实现 models/ports/repository/service + router;import-linter 第 11 契约

## 3. agent 侧

- [x] 3.1 QueryIn 纯增 memory_hint;generate 拼接段(系统模板零改动);react 首轮同款
- [x] 3.2 /internal/extract-memory 端点(LLM 提取,新 prompt 撰写资产)
- [x] 3.3 agent 测试:hint 拼接位置/空 hint 零回归/extract 应答解析

## 4. chat 挂接与隐式形成

- [x] 4.1 MemoryReader 端口+组合根(009 embedding 通道复用);query/start_stream 召回挂接(失败降级)
- [x] 4.2 显式「记住」短路(护栏检测+落库+确认答复)
- [x] 4.3 隐式形成后台任务(SUCCESS 轮触发;提取→PII 检测→去重落库)
- [x] 4.4 make contracts 重生成

## 5. 验证与收尾

- [x] 5.1 016 评估 smoke 5/5 复验(零回归红线)
- [x] 5.2 真 LLM 冒烟:「请记住我偏好月度口径」→确认答复→后续问答召回注入(审计/agent 日志可见 hint);PII 拒绝;自管理端点全链;隐式记忆落库可见
- [x] 5.3 R1 独立评审:2 高(H1 operlog 时序/H2 injection 记忆通道)+4 中(M1 编辑毁向量/M2 短路轮排除+触发面/M3 流式漏挂/M4 租户同构三漂移)+5 低全处置;直证 +4 用例
- [x] 5.4 make check 全绿 + HANDOFF + 归档(delta sync)
