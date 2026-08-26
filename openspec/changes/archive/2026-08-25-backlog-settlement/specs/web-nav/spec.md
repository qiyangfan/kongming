# web-nav 规格增量:查询错误态统一渲染

## ADDED Requirements

### Requirement: 查询错误态统一渲染

用户面与管理面所有数据查询(react-query useQuery 族)失败时 SHALL
以统一形态显式渲染:role=alert 的错误提示(「加载失败」语义文案,
支持变体文案),不得伪装成空态、不得静默空白、不得只依赖 console。
该形态 SHALL 由共享组件单一实现承载;新增页面 MUST 复用共享组件,
不得各自手写错误分支或省略错误态(038 用量页为首个范式,本增量
收敛为全站组件)。

#### Scenario: 查询失败显式呈现

- GIVEN 某管理页数据查询返回 5xx
- WHEN 页面渲染
- THEN 该页显示 role=alert 的加载失败提示(非空白非空态伪装)

#### Scenario: 共享组件单一实现

- GIVEN 多个页面均消费查询错误态
- WHEN 检查实现
- THEN 错误态由同一共享组件渲染,无页面级手写副本
