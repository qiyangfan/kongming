# 054-gates-env-parity 实施任务

> 纪律:每步改完跑对应子门禁;全部完成后 `make check` 全绿 + CI 实跑绿方可归档。

## 1. CI 门禁等价

- [x] 1.1 ci.yml api job 在 mypy 步骤后追加 `uv run pyright kongming`(与 Makefile api-check 同参);本地以 `act` 或推送后 CI 实跑验证步骤可执行。
- [x] 1.2 ci.yml agent job 同样追加 `uv run pyright kongming_agent`。
- [x] 1.3 首跑若暴露 pyright 报错(本地/CI 环境差异),逐条修复,禁止以放宽 pyrightconfig 收场;修复记录进 HANDOFF 教训节。

## 2. `.env.example` 对齐运行现状

- [x] 2.1 `KM_AGENT_LLM_BASE_URL` 示例值改为 `http://127.0.0.1:8787/v1`(muniu 网关,ADR-007),行注释保留 dashscope 直连值并注明「回滚 = 改回此值」。
- [x] 2.2 补 `# KM_SQLGUARD_MODE=shadow` 与 `# KM_AGENT_SQLGUARD_MODE=shadow` 示例行(api/agent 两个文件如各自独立 example 则各补各的),注明缺省 shadow、切主条件见 053 HANDOFF 归档门。
- [x] 2.3 通读全文件核对剩余键位与 `apps/api/kongming/config.py`、`apps/agent/kongming_agent/settings.py` 当前字段一致,删除/修正已失效注释。

## 3. 文档与收口

- [x] 3.1 HANDOFF 门禁小节补「make check × CI」对照表(api/agent/liannu/web/contracts 五行,列:本地命令、CI job、CI 步骤、差异说明),pyright 行标注本次补齐。
- [x] 3.2 `make check` 全绿(2026-09-13 本地全绿:api 896+agent 209+liannu 21,pyright 0 errors);push 后盯 CI 至绿(含新 pyright 步骤);归档本变更。
  【2026-09-24 收口】CI 实跑绿(run 36016385961:api/agent/liannu/web/contracts 五路
  通过,含 pyright 步骤);同批顺手修掉 pyright 严格面与 ruff format/check 门禁
  (056–059 新代码),CI 假绿面闭合。归档本变更。
