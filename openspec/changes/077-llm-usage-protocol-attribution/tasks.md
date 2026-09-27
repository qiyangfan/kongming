# 077-llm-usage-protocol-attribution 实施任务

## 1. 测试先行

- [ ] 1.1 失败测试:agent→api 内部上报携带 protocol,三通道(openai/anthropic/
      responses)各自透传,落库后 protocol 列值为对应通道;未携带时落 null 不报错;
      先跑红
- [ ] 1.2 失败测试:明细分页按 protocol 筛选只返回该协议行;同名 model 两条目
      流量可拆分;返回行含 protocol 列(null 行不炸);先跑红
- [ ] 1.3 失败测试:汇总按 protocol 过滤后 tokens/calls 计数只含该协议;
      不过滤时与现状逐字一致(零回归);无 system:llm-usage:view 时 403;先跑红

## 2. 实现(agent 面 + api 面 + 迁移)

- [ ] 2.1 迁移 `0032_llm_usage_protocol.py`:sys_llm_usage 增
      `protocol VARCHAR(16) NULL`;存量不回算;down_revision 按实际落地顺序
      与 069 的 0031 串联(不得双头)
      **验证**:`make api-check` 绿;迁移 up/down 往返
- [ ] 2.2 `llm.py`:`_report` 增 protocol 参数,三通道调用点各透传自身协议
      (缺省 openai,与 DEFAULT_PROTOCOL 语义一致);内部上报 DTO 增字段
      **验证**:1.1 转绿
- [ ] 2.3 `llmusage/repository.py`:落库 SQL 含 protocol;明细/汇总筛选支持
      protocol;`llmusage/router.py`:`UsageRecordIn` 与 summary/page 参数增
      protocol(可选,空=不过滤)
      **验证**:1.2、1.3 转绿;`make api-check` 绿

## 3. 契约与前端

- [ ] 3.1 `make contracts` 提交产物(ADR-005);CI 漂移门禁兜底
      **验证**:contracts/openapi.json diff 含新筛选参数与 protocol 列
- [ ] 3.2 前端用量明细页:protocol 列 + 筛选(类型由生成产物提供,禁手写)
      **验证**:`pnpm typecheck && pnpm lint` 绿

## 4. 收口

- [ ] 4.1 本地构造注册表两条目(model 同名、protocol 不同)各发一次调用,
      明细按 protocol 筛出两股流量;ERROR 行同样可见协议归属;记录于 apply
- [ ] 4.2 全量 `make check` 收口
