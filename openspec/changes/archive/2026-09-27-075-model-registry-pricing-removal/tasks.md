# 075-model-registry-pricing-removal 实施任务

## 1. 测试先行:字段移除与向后兼容(宪法 Article III)

- [x] 1.1 失败测试:条目携带 `price_input`/`price_output` 时正常生效
      (不告警、不拒配、不影响路由)——与未知键同形态;先跑红
- [x] 1.2 失败测试:`ModelEntry` 不含计价字段(构造与访问面收缩),
      且注册表加载对缺该字段的条目零差异;先跑红

## 2. 代码与规格同步

- [x] 2.1 `settings.py` 删除 `ModelEntry.price_input/price_output` 与
      JSON 解析分支(:120-127、:150-151);解析逻辑对未知键静默忽略
      **验证**:1.1、1.2 转绿;`test_model_routing.py`/`test_llm_protocol.py`
      既有用例零回归
- [x] 2.2 spec delta sync 进 `openspec/specs/multi-model-routing/`
      (移除计价子句,新增「未知键不拒配」Scenario)
      **验证**:spec 与代码行为逐条对得上(无幽灵引用)
- [x] 2.3 复核 `docs/model-providers.md`、`.env.example`、`evals/README.md`
      确无计价登记(取证结论),无需连带改动
      **验证**:`grep -rn "price_input\|price_output" . --include=*.md
      --include=*.example --include=*.yaml` 零命中

## 3. 收口

- [x] 3.1 全量 `make check` 收口
- [x] 3.2 apply 记录留痕:本变更的规格出处、取证表与判定理由
      (即 proposal「Why」一节)随变更归档,供后续「要不要把计价接进成本轨」
      的提案直接引用,不重复取证
