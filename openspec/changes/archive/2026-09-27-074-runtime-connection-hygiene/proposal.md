# 074-runtime-connection-hygiene 提案:运行期连接弹性与部署栈就绪依赖

## Why

两处都不产生新功能,但都在自建部署里会真实咬人,且修法都是标准动作而非自创机制。

**一、控制面连接池无任何弹性配置。** `apps/api/kongming/db.py:25`:

```python
create_async_engine(get_settings().database_url, pool_size=5, max_overflow=5)
```

- 无 `pool_pre_ping`:连接被对端(中间盒 NAT、PG 重启、云代理空闲回收)掐断后,
  检出即用会直接抛错,表现为**间歇性 500**——最难自查的一类故障。
- 无 `pool_recycle`:长驻进程的连接长期不换代,同样落在上面的坑里。
- 池容量硬编码:多副本部署或高并发租户场景下,运维没有任何调节入口,
  改它必须改代码发版。同仓已有先例说明这不是小题大做——062 刚把
  `chat/router.py` 的 inline `limit=1000` 收成 `Settings.chat_result_max_rows`,
  理由正是「同一语义两个来源、其中一个不可配置」。

**二、compose 栈的依赖只到「容器起来了」,没到「服务能用了」。**
`infra/docker-compose.yml` 里 postgres/redis 有 healthcheck,api/agent 没有;
agent 的依赖写作 `depends_on: [api]`(无 condition)。api 自身启动期是
fail-closed 的(`main.py:192`「DB 不可达则拒启」),从容器启动到 uvicorn
真正 listen 之间有播种/cipher/引擎自检一段距离;这段窗口内 agent 已经在
接流量,首轮问答失败。另外 api/agent/redis 均无 `restart` 策略——
一次瞬时故障就是永久停摆的栈。

## What Changes

1. **连接池弹性**:控制面 engine 增加连接检出前存活校验(`pool_pre_ping`)
   与连接最大存活周期(`pool_recycle`);**容量可配置**(`pool_size`/
   `max_overflow`,缺省维持 5/5,行为零变化)。
2. **部署栈就绪依赖**:compose 为 api/agent 声明 healthcheck(两者均已有
   `/healthz` 端点);agent 依赖 api 的**健康状态**而非启动状态;
   api/agent/redis 补 `restart: unless-stopped` 策略。
3. **零业务行为变更**:本变更不改任何查询语义、路由、契约与 schema。

## Non-goals

- **PG 侧 `statement_timeout`(查询超时治理)**:真实缺口但语义是产品决策——
  「超长查询被库侧掐断」与当前「调用侧 30s 回调超时放弃、PG 侧继续跑完」
  是两种可观测行为,后者已由 `agent_timeout_sec` 兜底用户侧。
  **重估条件(写死)**:出现因长查询占满连接池导致的级联超时,或产品接受
  「查询超时」作为用户可见错误态。
- **连接池指标 / APM / 告警**:061 Non-goals 已明确拒绝(无采集后端与消费需求),
  且 `prometheus-client` 的「先引后不用」代价已有前例。
- **只读池/读写分离的池拓扑治理**:008 已有只读执行池,其拓扑演进属
  datasource 域,不在本变更。
- **多副本/水平扩缩容的容量规划**:需要压测数据支撑,不拍脑袋定默认值;
  本变更只把「可调」这件事补上。
- **改 Dockerfile / 镜像构建**:060(fix-container-image-build)的领地,
  两变更边界清晰,不越界。

## Capabilities

### New Capabilities

- runtime-resilience:运行期韧性——数据库连接池弹性(存活校验、可配容量、
  连接换代)与部署栈就绪依赖(健康检查、健康条件依赖、重启策略)。

## Impact

- **落点**:`apps/api/kongming/db.py`、`apps/api/kongming/config.py`(3 个
  Settings 字段)、`.env.example`、`infra/docker-compose.yml`。
- **契约**:无 OpenAPI 变更(设置项不入契约,`.env.example` 登记即可)。
- **ADR**:不涉及。ADR-004 无 schema 变更;ADR-001 技术栈不变。
- **门禁**:纯接线 + 配置变更,回归面是既有 api 测试全集;compose 变更
  以冒烟(起栈 → `/healthz` → 一次问答)验证。
- **与在流变更的关系**:060 同时改 compose,两变更的落点都在
  `infra/docker-compose.yml`;本变更只增 healthcheck/condition/restart,
  不动 build context 与 Dockerfile,合并时按行域区分,无文本冲突。
