# 012 管理台状态启停范式统一 — 设计

## Context

现状(动机见 proposal.md):用户页状态=静态 Badge(启停四步走对话框);
数据源页=Badge+行内文字按钮(008 已带失败反馈);用户启停无专用端点;
禁用不吊销 refresh。约束:refresh 吊销管线已存在(007 改密场景注入
RefreshFamilyStore);数据源已有 enable/disable 端点范式(008);web
裸元素红线(Switch 须组件库)。

## Goals / Non-Goals

**Goals:**
- 用户启停专用端点(与数据源同范式)+ 禁用任一路径吊销 refresh 家族
- StatusSwitch 单一公共组件统一两页,失败回弹工艺固化

**Non-Goals:**(proposal Non-goals 之外的设计边界)
- 不动 enable 之后的行为(无吊销需求)
- 不引入乐观更新(受控真值优先,简单正确)

## Decisions

### D1: 专用端点而非通用 PUT 组装

行内开关若走通用更新端点,需 role_keys→role_ids 反查(依赖 roles 列表
就绪,踩 007「roles 未就绪静默清空绑定」坑)且载荷携带无关字段。
专用 `POST /system/users/{id}/enable|disable` 意图单一、与数据源 008
同构、服务端保护集中。编辑对话框路径保留(全字段编辑是合法用例)。

### D2: 禁用吊销收口在 service 单点

吊销逻辑放 service 内部私有段(`_set_status` 或等价),专用端点与
update_user 的 status→DISABLED 分支共同调用——任一禁用路径都吊销,
防「端点吊销、编辑路径漏吊销」的分叉。复用 007 注入的 RefreshFamilyStore
(改密吊销同款),失败语义与改密一致(Redis 不可用时异常上抛)。

### D3: StatusSwitch 受控真值,不做乐观更新

`checked` 恒由父层从 react-query 缓存计算(服务器真值);点击触发
mutation,**pending 期全局禁用所有行**(R2-F5:单行禁用下并发 mutate 会
覆盖单槽 toggleId 归因,先完成者的 onSuccess 清空 toggleId 可使后者的
pending 指示与错误文案双丢失——回到全局互斥,与旧行为对齐,结构性杜绝
并发);失败后缓存未变→开关自然回弹,错误经行内 `role=alert`+`aria-
describedby` 关联暴露。不做本地乐观翻转(省去手动回滚逻辑,且与 008 教训
「失败静默」天然互斥)。组件签名:

```
StatusSwitch({ active, pending, error, onToggle, ariaLabel })
```

父层以「正在操作的行 id」管理 error 归属(pending 全局,错误只挂最后
失败行)。

### D4: shadcn switch + 语义类核验(006 R4 纪律)

`pnpm dlx shadcn@latest add switch`(radix-ui 聚合包,预期零新依赖);
引入后核验其语义类(--primary/--background/--muted 等开关轨道/手柄色)
在本仓 index.css @theme 有定义,浏览器实测激活/聚焦态。

### D5: 审计动作 user.enable / user.disable

与 datasource.enable/disable(008 五动作)对齐;动作清单测试实际调用两端点
并把动作加入 expected 断言集(子集式 `expected <= actions`,R2-F4)。
detail 载 username,与 user.update 同形态。

## Risks / Trade-offs

- [禁用即吊销在 Redis 不可用时 409/500 语义] → 与改密吊销既有降级语义
  一致(007 挂账「吊销 fail-open 窗口」同源),不新增分叉。
- [开关无确认误触] → 二元可逆操作+失败回弹,确认弹窗属过度设计
  (Non-goal 已立);自禁/超管禁有服务端 409 兜底且文案可见。
- [行内开关与对话框 Select 状态短暂不一致] → 对话框打开时取行数据快照,
  提交后 invalidate 列表;两路同收口 service,不一致窗口毫秒级且自愈。
- [switch 组件 token 缺失重蹈 006 R4] → D4 核验步骤入 tasks,冒烟实测。

## Migration Plan

纯增量:2 新端点(无破坏)+ web 组件替换;部署=常规发版;回滚=revert。
契约经 `make contracts` 重生成(+2 路径)。

## Open Questions

(无——吊销语义、端点形态、组件工艺均已定;实现期如遇 refresh 吊销接口
形状不匹配,以复用改密路径的既有调用形态为准)
