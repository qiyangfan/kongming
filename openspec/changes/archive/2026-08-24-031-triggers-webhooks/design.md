# 031 triggers-webhooks 技术设计

## Context

029 引擎 + 025 Bot 提供了执行面;本变更补触发面。约束:零新依赖
(cron 手写);单进程部署(调度互斥从简);触发执行全走既有入口。

## Decisions

### D1: 定义模型(0019)

sys_trigger_definition:id uuid7 / tenant_id / tg_key / name /
type VARCHAR CHECK(CRON|WEBHOOK)/ target_type CHECK(workflow|bot)/
target_key / cron_expr(可空,CRON 必填)/ min_interval_sec int 默认 60/
webhook_path uuid 随机(WEBHOOK 创建时生成,不可改)/ secret_cipher
(AES-GCM,WEBHOOK 必填)/ adapter VARCHAR CHECK(custom|github) 默认
custom / notify_url 可空 / enabled bool / created_at/updated_at;
唯一(tenant_id, tg_key),webhook_path 全局唯一索引。

sys_trigger_run:id / tenant_id / trigger_id / status CHECK(SUCCESS/
FAILED/SKIPPED)/ error / duration_ms / target_run_ref(varchar,
工作流 run id 或空)/ created_at;索引(trigger_id, created_at DESC)。

### D2: cron 解析(models.cron,纯函数)

五字段(分 时 日 月 周),支持 `*` `*/n` `a-b` `a,b,c` 单值;dom/dow
互斥语义按 Vixie 惯例(双非 * 取并集——简化:双非 * 直接拒,报
「仅支持其一为 *」);dow 0/7=周日。next_fire(expr, after) 逐分钟
步进扫描(上限 366 天),30s tick 精度下足够(步进 1min 对齐)。
非法表达式保存期 400。

### D3: 调度器(scheduler.py)

lifespan 后台任务:每 30s 一轮——查 enabled CRON 触发器,
`next_fire <= now 且 last_fired 满足 min_interval` → 触发(更新
last_fired/next_fire_at 同事务);进行中标记(process 内 dict[
trigger_id]=task,在跑再到期→记 SKIPPED run)。触发执行
asyncio.create_task 独立会话(015/017 短事务先例)。

### D4: webhook 入站与适配器

POST /webhooks/{path_uuid}:path 查行(全局唯一)→ 读 body(≤1MB)
→ X-Signature 校验(hmac.compare_digest,hex 形态;secret 解密仅
瞬时)→ 失败 401 同形(不存在/签名错/禁用不区分,防探测)→
适配器标准化:custom=整 payload 作 inputs;github=取
X-GitHub-Event + payload 提要(ref/repository.full_name/commits
长度/pusher)→ {event, repo, ref, summary}→ 202 即返,异步
create_task 执行(独立会话)。

### D5: 目标执行与历史

workflow:029 service.run(独立会话装配 runner 组合,trigger_source
语义记 trigger_run;user_id=定义创建者);bot:025 service.run
(channel="cron"|"webhook")。sys_trigger_run 终态全落(SKIPPED
含并发/间隔场景)。通知:notify_url 非空 → 执行完成后 POST 摘要
(X-Kongming-Signature 签名;先经 029 assert_url_allowed SSRF 判定,
拒即记 run error 段;5s 超时)。

### D6: 测试策略

- cron 解析:全形态/非法/双非*拒/next_fire 边界(月末/周日 0/7)。
- 签名:正确/错误/缺失/禁用/不存在全 401 或 404 同形;payload
  >1MB 413。
- 调度:mock 时钟到期触发/min_interval 跳过/进行中 SKIPPED。
- 适配器:github push 标准化/custom 透传。
- 目标执行:workflow 触发落双表(trigger_run+workflow_run)/bot
  channel 溯源/SSRF 通知拒。
- 管理面:CRUD/启停/404 同形/密钥不回显/历史分页。

## Risks / Trade-offs

- [单进程调度重启丢窗] → next_fire_at 持久化,重启后首轮扫描补
  到期(miss 窗口 ≤ 重启时长;exactly-once 不立约,挂账多实例)。
- [签名密钥泄漏面] → AES-GCM 落库,任何接口不回显(创建响应
  一次性展示明文,与 025 api-key 同哲学)。
- [webhook 公开端点 DoS] → body ≤1MB + 签名校验前置(不验不过)
  + 202 即返;执行异步不占连接。

## Migration Plan

0019 两表;权限点 24 只增;lifespan 增调度任务(默认空转零开销);
回滚=删端点+停任务。

## Open Questions

- O1:失败重试策略(指数退避);O2:多实例调度互斥(pg advisory
  lock);O3:更多事件源;O4:trigger_run 与 workflow_run 双表
  关联展示(管理面跳转);O5:时区可配(固定 Asia/Shanghai)。
