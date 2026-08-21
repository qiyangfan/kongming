# 015 审计可视化与留存 — 实施任务

## 1. api:audit 域包骨架 + 契约调整 + 种子(D1)

- [x] 1.1 audit/ports.py(AuditRepo Protocol:sql_page/sql_detail/oper_page/角色 ALL 判定/commit)+repository.py(两表分页 CAST 可空绑定+详情)+service.py(行级可见性强制覆盖,零框架)+router.py(薄适配)
- [x] 1.2 pyproject import-linter:既有「datascope/audit 不依赖 Web 框架」audit 移出(datascope 保留);新增「audit 领域模型/用例/端口不依赖框架」(models/service/ports 禁 fastapi+sqlalchemy)
- [x] 1.3 auth/seed.py:PERMISSIONS 增 (system:audit:view,审计查看,9);种子断言更新(9 权限点,超管 9,DEVELOPER 推荐不变)

## 2. api:查询端点(D3,测试先行)

- [x] 2.1 测试先行:test_audit_query——sql/page 筛选(verdict/user_id/时间)+倒序;sql/{id} 详情完整 sql_text;非 ALL 强制本人(分页+详情 403 同形);无权限 403;oper/page 同款
- [x] 2.2 端点三件套注册(main.py)+契约重生成

## 3. api:ip 链路(D2,测试先行)

- [x] 3.1 测试先行:任意写操作(如 user.create)后 sys_oper_log.ip 非空(TestClient host="testclient");无请求上下文(后台任务直调 add)ip=None 不炸
- [x] 3.2 audit/context.py(client_ip_var+ClientIpMiddleware);main.py 注册;SqlOperLogRepo.add 读 contextvar 填 ip 列

## 4. api:导出审计(D4,010⑦清零,测试先行)

- [x] 4.1 测试先行:导出 CSV 后 sys_oper_log 含 chat.export(detail fmt/rows/truncated);审计写失败不阻断导出(monkeypatch add 抛错,导出仍 200)
- [x] 4.2 chat/ports.py 加 OperLogger Protocol;ChatService 加 oper_logger 可选注入;export_result 落审计+commit;router 组合根绑定 SqlOperLogRepo

## 5. api:归档与重导入(D5,测试先行)

- [x] 5.1 config:audit_archive_days=180/audit_archive_dir="var/audit-archive"
- [x] 5.2 测试先行:test_audit_archive——构造超窗记录→run_archive 导出 gzip JSONL+manifest(SHA-256 校验过)+库内删除+窗口内保留;重跑幂等(不产重复文件);run_restore 恢复(幂等,重复导入跳过);manifest 篡改拒导入;归档失败不阻断审计写入(目录不可写,查询审计正常落)
- [x] 5.3 audit/archive.py:run_archive/run_restore(fetchmany 分批+流式 gzip)
- [x] 5.4 lifespan _daily_archive_loop(次日 03:00 薄壳)+端点 POST /audit/archive/run|restore(restore 路径穿越防护)

## 6. web:审计页(D6)

- [x] 6.1 lib/audit-api.ts 封装(类型全生成);AdminLayout 子导航「审计」(system:audit:view);main.tsx 路由
- [x] 6.2 AuditPage:toggle-group 双区(SQL 审计/操作日志);筛选+分页表+行内展开详情(完整 sql_text/detail pre);ip 列(操作日志区)
- [x] 6.3 tsc+eslint 零告警

## 7. 门禁与冒烟

- [x] 7.1 make check 全绿;make contracts 重生成(5 新端点)
- [x] 7.2 冒烟:①SQL 审计页筛选/详情展开完整 SQL;②操作日志页 ip 列非空(任意管理操作后);③导出 CSV 后操作日志 chat.export 可见;④非 ALL 用户(smoke-self 绑 audit:view SELF 角色)只见本人;⑤归档手动触发(构造超窗数据→run→文件+SHA-256 清单+库内删除→restore 恢复);⑥审计页导航守卫(无权限 403);Playwright MCP 留痕
- [x] 7.3 R1 独立评审→修复→复核→归档

## 8. R1 评审修复(0 严重透穿 5+建议 7)

- [x] 8.1 S1 补 chat delta(结果导出增补 chat.export 审计条款+scenario)
- [x] 8.2 S2 归档跳过分支增窗口覆盖校验(manifest_cutoff >= cutoff,days 调小不未导先删)+用例
- [x] 8.3 S3 归档进程锁(asyncio.Lock 串行,防并发交错损坏)+并发用例
- [x] 8.4 S4 窗口幂等措辞改导出日锚+跨日重复语义显式声明(delta/design)
- [x] 8.5 S5 脱敏测试锁定(重置密码 detail 无明文)
- [x] 8.6 B1 导出审计 rollback 包 suppress;B2 lifespan 任务收割;B3 审计页时间本地化;B4 时间范围筛选补 UI(oper user 筛选挂账);B5 proposal ip 句改 contextvar 口径;B6 Dockerfile --proxy-headers;B7 冗余 import 清理
- [x] 8.7 make check 全绿复核(api 441+agent 87+web)
