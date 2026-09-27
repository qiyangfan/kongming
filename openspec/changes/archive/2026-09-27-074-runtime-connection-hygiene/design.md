# 074-runtime-connection-hygiene 设计

## D1 为什么是 pre_ping + recycle,而不是只在故障时重连

不加任何配置时,SQLAlchemy 池的行为是「检出即用」:一条被对端静默关闭的
连接会在第一次复用时抛 `InterfaceError`/`ConnectionResetError`。这不是
致命错误——池会丢弃坏连接,下一次 checkout 拿到新的——但它**把一次失败
请求暴露给了用户**,而且失败时刻与连接死掉时刻无关(可能是 8 小时前死的),
排障时完全看不出因果。

两个手段覆盖不同侧面:

- `pool_pre_ping=True`:每次检出前一次轻量探活,坏连接在**交到用例之前**
  就被换掉。代价是每次 checkout 多一次往返(同机 PG 亚毫秒级),
  对以查询为主的代价可忽略。
- `pool_recycle`:给连接设最大年龄,长驻进程里主动换代,减少「死后被发现」
  的窗口。对没有中间盒的环境收益趋零,但成本同样趋零。

两者叠加是 SQLAlchemy 官方对生产部署的常规建议,不是自创机制;本变更
不引入连接池库、不换驱动。

## D2 为什么池容量要可配,而不是调一个「更好的默认值」

5/5 是引入时的猜测。真正需要它变小的场景(单机多副本、PG `max_connections`
吃紧)和需要变大的场景(高并发租户、长连接持有多的报表导出)方向相反,
不存在一个普适更优值。把它做成 `Settings` 字段,缺省维持 5/5:

- 行为零变化(既有部署无感);
- 运维获得调节入口,与 062 的 `chat_result_max_rows`、055 的
  `cost_reconcile_threshold` 同一形态——本仓的既定做法是
  「运维参数进 Settings 并登记 `.env.example`」,不是散落硬编码。

缺省值不变这一点很重要:本变更的价值在「可调 + 不断线」,
不在「猜一个新数字」。

## D3 compose 依赖为什么要到「健康」而不是「启动」

`depends_on: [api]` 的语义只是「api 容器先启动」。api 的启动期不是瞬时的:
它要做 cipher 构造、053 引擎自检、`seed_all` 播种(`main.py:197-208`),
且 DB 不可达时**直接拒启**(uvicorn 退出)。这段窗口里:

- 若 PG 尚未就绪,api 容器起来又退出——而没有 `restart` 策略,栈就永久停在
  半活状态,用户看到的是「能访问 web,一问就错」;
- 若 api 正常启动但还在播种,agent 的首个查询打到尚未 listen 的端口上。

改法是让依赖指向健康状态:api/agent 各自声明 healthcheck(端点现成),
agent 等 api `service_healthy` 再起,并给 api/agent/redis 补 restart 策略。
这不是新设施,是把 compose 已有的能力(`healthcheck` + `condition`)接到
现成的端点上。

## D4 为什么不做 statement_timeout(记录在案的真实缺口)

nl2sql 平台执行的是生成的 SQL,理论上可能跑很久。当前防线在调用侧:
api→agent 的 `/internal/sql/execute` 回调超时 30s,agent 放弃本轮;
但 **PG 侧的查询不会因此中止**,连接与后端进程继续被占用到查询自己结束。
这是一致的资源泄漏面,不是本变更顺手能补的:

- 库侧超时会把「跑得慢」变成「查询被中止」,是**用户可见的新错误态**,
  需要 spec 立约(sql-execution 域)、需要决定超时值、需要决定是否区分
  explain/导出路径——那是另一个能力域的事;
- 当前的实际风险受 chat 面 `chat_result_max_rows`(062 在流变更)与行级
  改写限制压制,尚未出现级联证据。

因此写进 Non-goals 并附重估条件,而不是留一个「以后再说」的空话。
