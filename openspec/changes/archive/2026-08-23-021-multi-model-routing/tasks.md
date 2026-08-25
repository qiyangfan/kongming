# 021 multi-model-routing 实施任务

## 1. Schema 与种子

- [x] 1.1 alembic 0010:sys_llm_usage(design D4;tenant_key 反解 tenant_id 语义)
- [x] 1.2 权限点 system:llm-usage:view(排序 16);种子断言更新

## 2. agent 注册表与路由

- [x] 2.1 settings:KM_AGENT_MODEL_REGISTRY_JSON/ROUTES_JSON/ALLOWED_HOSTS 解析(畸形剔除/重名拒/白名单拒配/空注册表回退;单测全形态)
- [x] 2.2 llm.py:多客户端池(主模型恒在)+ chat/chat_with_tools 加 task/trace_id 形参+单点路由回退;usage 采集
- [x] 2.3 apiclient.py report_usage(fire-and-forget,失败仅日志)
- [x] 2.4 节点接线:understand/generate/summarize/react_llm/titler/describer 传 task+trace_id(prompt 资产零改动)
- [x] 2.5 agent 测试:路由命中/回退/白名单拒配/usage 采集字段/上报失败不阻断

## 3. api 落库与查询

- [x] 3.1 llmusage 域包:POST /internal/llm-usage(内部令牌,批量≤64,tenant_key 反解失败 NULL)+ sys_llm_usage 批量插入
- [x] 3.2 GET /system/llm-usage/summary(聚合查询,system:llm-usage:view);import-linter 契约标注不适用(R1-L4:本域无领域层可护)
- [x] 3.3 api 测试:落库/畸形记录拒/聚合分组/403/内部令牌守门

## 4. 契约与验证

- [x] 4.1 make contracts 重生成;016 评估 recipe smoke 5/5 复验(单模型零回归红线)
- [x] 4.2 真 LLM 冒烟:注册 deepseek 风格第二条目(同 dashscope 端点不同模型名)配 SQL_GEN 路由,问答后查 sys_llm_usage 按任务分组的模型分布;上报失败形态(agent 停 api)主链路正常

## 5. 评审与收尾

- [x] 5.1 R1 独立评审:1 中(M1 路由静默回退)+6 低全处置(M1/L2/L4/L6/L7 修复,L5 明文取舍,L3/L8 挂账)
- [x] 5.2 R1 修复清零 + 复验(usage 字段钉/M1 双告警)+make check 全绿
- [x] 5.3 HANDOFF 更新 + 挂账;openspec archive(delta sync)
