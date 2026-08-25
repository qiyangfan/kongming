# 023 semantic-layer 实施任务

## 1. Schema 与种子

- [x] 1.1 alembic 0012:rag.table_relations + rag.metric_definitions(design D1)
- [x] 1.2 权限点 system:semantic:manage(排序 17);种子断言;datasource 删除级联补两表清理

## 2. semantic 域包(测试先行)

- [x] 2.1 失败测试:introspection 幂等/manual 不被覆盖/无 FK 不猜测/数据源删除级联
- [x] 2.2 失败测试:指标 CRUD 唯一约束/启用切换
- [x] 2.3 失败测试:注入组装(召回集合收窄/指标命中/top-5)
- [x] 2.4 实现 models/ports/repository/service/router;import-linter 第 12 契约

## 3. 链路注入

- [x] 3.1 rag retrieve 响应纯增两段(RecallEnricher 端口,组合根绑 semantic service)
- [x] 3.2 agent:RagContext 扩字段+retrieve 透传+generate 拼接段+react 观察可见
- [x] 3.3 agent 测试:拼接位置/空零回归

## 4. 管理面与契约

- [x] 4.1 端点全量(relations CRUD+introspect+metrics CRUD);403/归属校验
- [x] 4.2 make contracts;web /admin/semantic 第十子页(关系 Tab introspection 只读/manual 可编辑;指标 Tab)

## 5. 验证与收尾

- [x] 5.1 真 LLM 冒烟(全过):introspect sakila 55 FK 幂等;metric 定义+召回注入(metric_hints 命中「平均租金」口径;relations_hint 经两形态正则修复后正确收窄);端到端问答 ROUND 口径生效;评估 smoke 待 5.3 前复验
- [x] 5.2 R1 独立评审:2 高(H1 多列 FK 笛卡尔积实证/H2 端点缺数据源校验+张冠李戴)+2 中(M1 source 不翻转+假 id/M2 回退丢弃+ReAct 零收益)+8 低全处置;直证 +3 用例;make check 全绿
- [x] 5.3 HANDOFF + 挂账 + 归档
