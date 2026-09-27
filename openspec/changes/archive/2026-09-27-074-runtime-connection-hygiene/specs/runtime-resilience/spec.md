# runtime-resilience spec delta(074-runtime-connection-hygiene)

## ADDED Requirements

### Requirement: 数据库连接池弹性

控制面数据库连接池 SHALL 在连接交付给用例前验证其存活,使被对端(数据库重启、中间盒空闲回收、代理断开)静默关闭的连接不会以失败请求的形式暴露给用户。池容量(常规连接数与溢出上限)与连接最大存活周期 SHALL 为可配置项,缺省值 SHALL 与配置化之前的行为一致。

#### Scenario: 坏连接不暴露给用户

- GIVEN 池内某连接已被对端关闭
- WHEN 下一个请求从池中检出连接并执行查询
- THEN 该连接在交付前被识别并替换,请求正常完成

#### Scenario: 缺省配置零行为变化

- GIVEN 未显式配置池容量与连接存活周期
- WHEN 控制面启动并处理请求
- THEN 连接池行为与配置化之前逐字一致

#### Scenario: 容量可调生效

- GIVEN 运维将池容量配置为更小或更大的值
- WHEN 控制面重启
- THEN 池按新容量工作,无需改动代码

### Requirement: 部署栈就绪依赖

自建部署栈 SHALL 为控制面与 agent 面声明健康检查,并使 agent 面依赖控制面的**健康状态**而非容器启动状态;两个服务与缓存 SHALL 配置为在异常退出后自动重启。启动期执行准备工作的服务(播种、密钥校验、安全引擎自检)SHALL 在对外宣称健康之前完成这些工作。

#### Scenario: agent 等控制面健康后启动

- GIVEN 冷启动完整部署栈
- WHEN 控制面仍在启动期(尚未完成准备工作)
- THEN agent 面不开始接流量;控制面健康后才启动

#### Scenario: 异常退出自动恢复

- GIVEN 控制面进程异常退出
- WHEN 编排层检测到退出
- THEN 容器自动重启并恢复服务,不需人工干预

#### Scenario: 健康检查不泄漏内部面

- GIVEN agent 面的健康检查请求
- WHEN 命中健康检查端点
- THEN 该端点不要求内部通道令牌(与既有形态一致),且不暴露内部端点语义
