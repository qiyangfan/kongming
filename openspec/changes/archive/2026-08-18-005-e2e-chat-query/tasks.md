# 005 任务清单:端到端问答链路

> 安全规则类(require_perm/会话隔离/租户校验/内部通道)测试先行:
> 先写失败测试再实现。每个 task 完成后跑对应 make check 子集。

## 1. agent 面:LLM 与回调底座

- [x] 1.1 `kongming_agent/llm.py`:AsyncOpenAI 封装(chat 文本/chat_json,
      温度与 30s 超时参数化,base_url/model/key 走 settings);key 缺失时
      调用抛清晰错误(settings 既有 warn 升级为调用点报错)
- [x] 1.2 `kongming_agent/apiclient.py`:httpx AsyncClient 封装
      (X-Internal-Token);`sql_execute()`(三态响应模型)与
      `fetch_schema()`;HTTP 错误收敛为领域结果,不裸抛
- [x] 1.3 settings 增补:llm_timeout_sec(默认 30)、api_timeout_sec
      (默认 30)、schema 提示无关;`pyproject.toml` 加 sqlglot 依赖,uv sync

## 2. agent 面:节点接线(测试先行:mock LLM + mock apiclient)

- [x] 2.1 understand:history[-5:] 编号拼 user 模板;多轮/单轮分支;
      空输出兜底原问句;history 超 5 轮截断。测试:指代消解 prompt 组装、
      截断、兜底
- [x] 2.2 retrieve:调 fetch_schema 拼表/列/注释提示;失败降级空提示
      (不抛错);truncated 标注透传。测试:拼装格式、降级路径
- [x] 2.3 generate:SQL_GEN_SYSTEM 原文 + schema/错误喂回拼装;输出清洗
      (反引号/sql 前缀);成功后清 error。测试:清洗各形态、错误喂回拼入
- [x] 2.4 validate:sqlglot 预检四规则(非空/可解析/单语句/Select 根);
      非 Select 标 permission_block。测试:DELETE 直判、多语句、解析失败
- [x] 2.5 execute:回调 sql_execute;verdict 三态入 state;HTTP 异常收敛
      ERROR。测试:三态映射、异常收敛
- [x] 2.6 selfheal:写 error(validate 原因优先于 execute reason)、
      计数递增;路由:verdict=BLOCKED 或 permission_block → fail;
      retry≥2 → fail。测试:BLOCKED 不重试、ERROR 重试、耗尽 fail
- [x] 2.7 summarize:stats 本地计算(排 bool)+ insights JSON 解析降级链
      + 无数据短路。测试:非 JSON 降级、空结果、stats 计算
- [x] 2.8 图全链路测试(scripted mock LLM):首轮 PASS、ERROR→selfheal→
      PASS、BLOCKED→fail、重试耗尽→fail 四条路径;state 断言

## 3. agent 面:/query 端点

- [x] 3.1 `POST /query`:请求/响应模型(D1);trace_id 必填;graph.invoke
      编排;响应 verdict/retry_count/sql/summary/insights/stats/columns/rows
- [x] 3.2 端点测试:内部令牌 403(既有中间件)、缺 trace_id 422、
      mock graph 全链路响应形态

## 4. api 面:chat 域包(测试先行)

- [x] 4.1 失败测试先行:require_perm 无点 403/有点放行/撤销立即生效;
      会话越权 404 不区分;X-Tenant-Id 未绑定 403;persist=false 不落库;
      history 显式优先/后端补全
- [x] 4.2 `kongming/chat/models.py`:ChatRequest/ChatResponse 领域模型
      (零框架)+ chart 推断纯函数(D7 规则)
- [x] 4.3 `kongming/chat/repository.py`:chat_session/chat_message CRUD
      (新建会话/归属校验/落消息/最近 5 条 user 问句)
- [x] 4.4 `kongming/auth`:require_perm 依赖(DB 联查实时判定);
      种子 PERMISSIONS 加 chat:execute + DEVELOPER 推荐绑定增量补齐
- [x] 4.5 `kongming/chat/service.py`:编排(D5 顺序)+ agent 客户端
      (httpx,120s 超时,错误映射友好提示)+ 消息 status 三态映射
- [x] 4.6 `kongming/chat/router.py`:POST /chat/query 挂路由;main.py 注册
- [x] 4.7 `kongming/internal/meta.py`:/internal/meta/schema(D6;租户
      fail-closed 复用 004 语义;50 表上限配置项)
- [x] 4.8 api pyproject:httpx dev→主依赖;import-linter 契约补 chat 域
      (models 零框架)

## 5. 契约与门禁

- [x] 5.1 `make contracts`:openapi.json 重生成 + web 类型;web tsc 过
- [x] 5.2 `make check` 全绿(api/agent/web 各自门禁)

## 6. 冒烟(真 PG/Redis/agent 三进程)

- [x] 6.1 mock-LLM 全链路:curl /chat/query 走通(建会话/落消息/审计
      trace_id 一致)
- [x] 6.2 真 LLM(key 就位后):tenant_default.goods 活数据问答(如
      「连弩有多少库存」类),记录延迟与摘要质量;BLOCKED 路径(诱导写
      操作)验证不重试 + REJECTED 落库
- [x] 6.3 冒烟记录写入 apply 总结

## 7. 收尾

- [x] 7.1 HANDOFF.md 更新(当前状态/下一步梯队前移)
- [x] 7.2 审查循环(独立评审 → 修复 → 清零)
- [x] 7.3 openspec archive(等用户指令后执行)
