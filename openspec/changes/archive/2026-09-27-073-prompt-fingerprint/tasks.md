# 073-prompt-fingerprint 实施任务

## 1. 测试先行:指纹确定性与资产覆盖(宪法 Article III)

- [x] 1.1 失败测试:同一进程内两次计算指纹一致;跨进程(子解释器)
      再算仍一致(确定性,无随机盐/无字典序漂移);先跑红
- [x] 1.2 失败测试:任一资产文件内容变更 → 指纹变化
      (对四个 prompt 文件逐一验证,不漏文件);先跑红
- [x] 1.3 失败测试:资产内容还原 → 指纹还原(哈希无状态);先跑红
- [x] 1.4 失败测试:`GET /internal/prompt-fingerprint` 无内部令牌返回 403
      (与 `/internal/*` 同款守门,ADR-002),正确令牌返回指纹;先跑红

## 2. agent 面实现

- [x] 2.1 新增指纹纯函数(纯 stdlib,零依赖;按固定顺序拼接四份资产后
      取 SHA-256 短十六进制);启动时算一次
      **验证**:1.1、1.2、1.3 转绿;mypy strict + pyright strict 收敛
- [x] 2.2 `main.py` 新增 `GET /internal/prompt-fingerprint`(复用既有
      `require_internal_token` 中间件,healthz 豁免清单不变)
      **验证**:1.4 转绿;import-linter 契约绿(agent 仍零 DB/redis)

## 3. 评估链路接线

- [x] 3.1 `evals/test_eval.py` 运行开始时取指纹,打印在结果首行
      (与四组分数同屏);指纹不可得时显式标注「指纹不可得」
      (SHALL NOT 静默跳过)
      **验证**:`KM_EVAL_ADMIN_PASSWORD=... uv run pytest evals -m smoke`
      输出含指纹行
- [x] 3.2 `evals/README.md` 基线表增加指纹列;补一段「跨指纹比对」
      的输出示例与说明(标注不可比而非判劣化)
      **验证**:README 与代码行为一致(无幽灵引用)
- [x] 3.3 评估输出增加跨指纹判定:若与上次基线指纹不同,
      四组分数后输出「跨 prompt 版本,通过率不可直接比较」并列出两侧指纹
      **验证**:构造两个不同指纹(monkeypatch 资产)输出含标注

## 4. 契约与收口

- [x] 4.1 `make contracts` 重新生成并提交产物(ADR-005)
      **验证**:`git diff contracts/openapi.json` 仅新增该端点,无意外差异
- [x] 4.2 与两项并行变更的接线确认:(a) 065(eval-ci-gate)的红线判定读指纹
      后再决定可比性;(b) **069-chat-quality-observability 的 `prompt_version`
      与本变更共用同一枚指纹纯函数**——以先完成者为准、后完成者复用,
      不得出现两份指纹算法;若 069 先行落地,本变更的 2.1 改为复用其实现
      **验证**:全仓 `grep -rn "sha256\|blake2" apps/agent` 只有一处指纹实现
- [x] 4.3 全量 `make check` 收口

> 补记(评估实测 2026-09-27):
> - `.example-data` 已灌入(131M sakila+olist);`sakila_dyn` 归属 default 租户已修正。
> - LLM 经 DASHSCOPE/muniu 可用;**smoke 6/6 全绿**,full 1/1,multiturn 10/12。
> - multiturn 两例失败为**生成质量信号**(多轮指代下 gen 与 gold 结果集不一致),
>   非链路缺陷——正是 065 红线要盯的回归面,记入基线即可。
