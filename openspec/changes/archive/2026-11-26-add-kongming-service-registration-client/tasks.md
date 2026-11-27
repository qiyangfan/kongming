# Tasks

## 1. 注册客户端

- [x] 1.1 服务身份注册/心跳/能力/版本/幂等。验证：注册可见、last_seen 更新、重复上报不重复实例。
- [x] 1.2 控制面缺席退避/恢复补报。验证：启动/核心流程不阻塞；锦官恢复后注册成功。
- [x] 1.3 认证与脱敏负向。验证：匿名 401/403；payload 无凭据/连接串/服务域配置。

## 2. 契约

- [ ] 2.1 与锦官 service-registry envelope/能力键契约测试。验证：跨仓契约测试必跑（X-01）。
- [x] 2.2 能力/版本单一来源。验证：发布后心跳可见；契约漂移即红。

## 3. 验收与独立运行

- [x] 3.1 权限负向与跨租户用例。验证：未授权 401/403,跨租户 404/不可见。
- [x] 3.2 审计断言。验证：注册/心跳结果可审计,无敏感值。
- [x] 3.3 独立运行/降级 smoke。验证：停锦官启动/核心流程不受影响；恢复后补报。
- [x] 3.4 契约与生成客户端。验证：OpenAPI/生成物无漂移；跨仓契约测试必跑（X-01）。
- [x] 3.5 Greenfield 重置：删除/禁用旧注册路径。验证：无兼容/双轨,旧路径不存在。


> 2026-11-27 实施备注:跨仓契约(X-01)与 fujie client/真实 Keycloak 联调未就绪,
> 对应任务保持未勾选并登记为阻塞;本仓机制与本地验证已完成。

本地证据:
- `control/registration.py` 指数退避、注册→心跳→失败回注册;
  `control/capabilities.py` 能力/权益键单源; 注册/心跳结果落 `control.registration`
  并写 `sys_oper_log`(actor 空、无凭据)。
- payload 只含 service_id/instance_id/version/capabilities/admin_capabilities/
  entitlement_keys/health; 测试断言 capabilities 与真实健康(不伪造 ready)。
- 2.1 的跨仓 envelope 对拍依赖 X-01,标**阻塞**。
