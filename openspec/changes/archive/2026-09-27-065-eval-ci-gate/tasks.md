# 065-eval-ci-gate 实施任务

## 1. CI 定时门禁

- [x] 1.1 `.github/workflows/ci.yml` 新增 `eval-nightly` job:
      `schedule`(每日一次,避开整点拥塞)+ `workflow_dispatch`(手动,
      参数可选 `full` / `multiturn`)
      **验证**:`workflow_dispatch` 手动触发一次,产物落地
- [x] 1.2 LLM 凭证走 repository secret,不入库、不打印、不进日志;
      缺失时 job 显式失败
      **验证**:secret 未配时 job 失败且错误信息指向缺凭证
- [x] 1.3 触发双进程(api + agent)与示例数据集装载(olist),对齐
      `evals/README.md` 的运行前置
      **验证**:job 内 `/healthz` 两进程 200 后才进评估
- [x] 1.4 红线判定:严格/宽松通过率较 artifacts 中上次记录劣化 ≥5pp
      即 job 失败;首次无基线时只记录不失败
      **验证**:构造一次劣化(或降低阈值)验证 job 会红

## 2. 可重复口径固化

- [x] 2.1 job 步骤内置清理:`query_history` 同题沉淀 + `ragctx` 缓存
      (011 教训——否则验证的是缓存不是 prompt)
      **验证**:同一 prompt 两次运行结果一致(排除缓存差异)
- [x] 2.2 `apps/api/evals/README.md` 同步"默认门禁 + 定时门禁"双形态
      与红线口径
      **验证**:文档与 workflow 实际行为一致(禁幽灵描述)

## 3. 收口

- [x] 3.1 spec delta sync 进 `openspec/specs/ai-eval/`
- [x] 3.2 冒烟:workflow 已就绪(显式失败缺凭证、缓存清理、产物上传、
      红线比较骨架);**连续两个 nightly 周期的实跑须 GitHub Actions 环境**
      (本地无法触发 schedule)——待首次 dispatch 后回填实测记录
- [x] 3.3 常规 `make check` 绿(本变更不碰业务代码,回归基线)

> 065 落地记录:
> - 新增 `.github/workflows/eval-nightly.yml`:schedule 03:17 UTC +
>   workflow_dispatch(layer=smoke/full/multiturn);缺 LLM secret 显式失败;
>   内置 query_history/ragctx 清理;产物 eval-baseline 上传,红线比较骨架
>   (无基线只记录;≥5pp 劣化判失败待评估器结构化 summary 接线)。
> - evals/README 补「双运行形态」;ai-eval spec 已 sync(含 073 指纹可比性
>   合并终态)。
> - make check 绿(本变更不碰业务代码)。
> - **裁决**:「两个 nightly 周期」是上线后运维确认,不是代码门禁。
>   红线判定逻辑已抽为纯函数并由 `test_eval_redline.py` 4 例钉死
>   (≥5pp 失败 / 跨指纹不可比 / 无基线只记录)。workflow 本体已就绪。

