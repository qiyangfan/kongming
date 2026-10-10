#!/usr/bin/env bash
# 示例数据集装载(sakila + olist)→ Kongming dev 库。
# sakila 装两份:tenant_sakila schema(租户路径)+ 独立库 sakila_ext
# (动态数据源 sakila_dyn 的数据体,外部业务库形态——不复用租户 schema,
#  避免同一份数据出现"工作空间 + 动态源"双入口)。
# 幂等:已装载则跳过数据(注释/租户注册可重放)。
# 用法:infra/example-data/load.sh(需 postgres 容器在跑)
#   KM_PG_CONTAINER=<容器名>(本机 env 栈=env-postgres),缺省 kongming-postgres-1
#   KM_PG_USER / KM_PG_PASSWORD:执行角色(缺省 kongming)。上游 dump 依赖
#   DISABLE TRIGGER ALL 打乱序装载+提速——非表属主超管会拒(已实测),故
#   数据段以单事务(-1)执行并要求具备触发器特权;env 栈传
#   KM_PG_USER=postgres KM_PG_PASSWORD=…(容器内超管)。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DATA="$ROOT/.example-data"
CT="${KM_PG_CONTAINER:-kongming-postgres-1}"
PG_USER="${KM_PG_USER:-kongming}"
PG_PASS="${KM_PG_PASSWORD:-}"
if [ -n "$PG_PASS" ]; then
  PSQL=(podman exec -i "$CT" env PGPASSWORD="$PG_PASS" psql -U "$PG_USER" -d kongming -v ON_ERROR_STOP=1)
  PSQL_EXT=(podman exec -i "$CT" env PGPASSWORD="$PG_PASS" psql -U "$PG_USER" -d sakila_ext -v ON_ERROR_STOP=1)
else
  PSQL=(podman exec -i "$CT" psql -U "$PG_USER" -d kongming -v ON_ERROR_STOP=1)
  PSQL_EXT=(podman exec -i "$CT" psql -U "$PG_USER" -d sakila_ext -v ON_ERROR_STOP=1)
fi

[ -d "$DATA/sakila" ] || { echo "缺 $DATA/sakila"; exit 1; }
[ -d "$DATA/olist" ] || { echo "缺 $DATA/olist"; exit 1; }

echo "== 1/6 sakila schema → tenant_sakila"
sakila_tables=$("${PSQL[@]}" -tA -c \
  "SELECT count(*) FROM information_schema.tables
   WHERE table_schema='tenant_sakila' AND table_type IN ('BASE TABLE','VIEW')")
if [ "$sakila_tables" -ge 21 ]; then
  echo "    tenant_sakila 已有 $sakila_tables 个对象,schema 段跳过"
else
  "${PSQL[@]}" -c "CREATE SCHEMA IF NOT EXISTS tenant_sakila"
  # 预处理:search_path 改租户 schema;去超管专属语句(OWNER/LANGUAGE/GRANT/
  # REVOKE/public schema 注释)。DISABLE|ENABLE TRIGGER ALL 只在数据段
  # (insert-data.sql),由数据段以具备触发器特权的角色单事务执行,schema 段
  # 无需剥(dump 的 schema.sql 实测 0 条,剥了也是无操作,徒增误解)
  # 注意:sed 依赖上游 dump 的精确文本,装载后第 6 步断言兜底(防上游改格式静默装错 schema)
  sed -e 's/SET search_path = public, pg_catalog;/SET search_path = tenant_sakila, pg_catalog;/' \
      -e '/OWNER TO postgres;/d' \
      -e '/PROCEDURAL LANGUAGE/d' \
      -e '/^GRANT /d' \
      -e '/^REVOKE /d' \
      -e "/^COMMENT ON SCHEMA public IS/d" \
      "$DATA/sakila/postgres-sakila-schema.sql" | "${PSQL[@]}"
fi

sakila_rows=$("${PSQL[@]}" -tA -c "SELECT count(*) FROM tenant_sakila.film")
if [ "$sakila_rows" = "0" ]; then
  echo "== 2/6 sakila 数据(231k 行 INSERT,约一两分钟)"
  sed -e 's/SET search_path = public, pg_catalog;/SET search_path = tenant_sakila, pg_catalog;/' \
      "$DATA/sakila/postgres-sakila-insert-data.sql" | "${PSQL[@]}" -1 -q
else
  echo "== 2/6 sakila 数据已存在(film=$sakila_rows 行),跳过"
fi

echo "== 3/6 olist 建表 + COPY"
"${PSQL[@]}" -c "CREATE SCHEMA IF NOT EXISTS tenant_olist"
"${PSQL[@]}" -c "SET search_path = tenant_olist" -f - < "$ROOT/infra/example-data/olist-schema.sql"
# 文件名 → 表名
declare -a pairs=(
  "olist_orders_dataset.csv:orders"
  "olist_order_items_dataset.csv:order_items"
  "olist_customers_dataset.csv:customers"
  "olist_order_payments_dataset.csv:order_payments"
  "olist_order_reviews_dataset.csv:order_reviews"
  "olist_products_dataset.csv:products"
  "olist_sellers_dataset.csv:sellers"
  "olist_geolocation_dataset.csv:geolocation"
  "product_category_name_translation.csv:category_translation"
)
for pair in "${pairs[@]}"; do
  csv="${pair%%:*}"; table="${pair##*:}"
  rows=$("${PSQL[@]}" -tA -c "SELECT count(*) FROM tenant_olist.$table" 2>/dev/null || echo 0)
  if [ "$rows" != "0" ]; then
    echo "    $table 已有 $rows 行,跳过"
    continue
  fi
  echo "    COPY $csv → $table"
  "${PSQL[@]}" -c "COPY tenant_olist.$table FROM STDIN WITH (FORMAT csv, HEADER true)" \
    < "$DATA/olist/$csv"
done

echo "== 4/6 中文注释(两租户)"
"${PSQL[@]}" -f - < "$ROOT/infra/example-data/comments.sql"

# 业务 schema 只读授权:api 以 kongming 角色访问;超管(KM_PG_USER=postgres)
# 装载后表属主=postgres,不授权则 api 侧 403/权限拒。属主=kongming 时本授权
# 幂等无害;GRANT 失败(执行角色无授权权)即 set -e 显式中止
echo "== 4.5/6 业务 schema 只读授权 → kongming"
"${PSQL[@]}" -c "GRANT USAGE ON SCHEMA tenant_sakila, tenant_olist TO kongming"
"${PSQL[@]}" -c "GRANT SELECT ON ALL TABLES IN SCHEMA tenant_sakila, tenant_olist TO kongming"

echo "== 5/6 租户注册 + admin 绑定"
"${PSQL[@]}" <<'SQL'
INSERT INTO public.sys_tenant (id, tenant_key, tenant_name, status)
SELECT gen_random_uuid(), k, n, 'ACTIVE'
FROM (VALUES ('sakila', 'Sakila 影视租赁'), ('olist', 'Olist 巴西电商')) AS v(k, n)
WHERE NOT EXISTS (SELECT 1 FROM public.sys_tenant t WHERE t.tenant_key = v.k);
INSERT INTO public.sys_user_tenant (user_id, tenant_id)
SELECT u.id, t.id FROM public.sys_user u, public.sys_tenant t
WHERE u.username = 'admin' AND t.tenant_key IN ('sakila', 'olist')
ON CONFLICT DO NOTHING;
SQL

echo "== 6/6 sakila → 独立库 sakila_ext(动态源 sakila_dyn 数据体,外部库形态)"
ext_exists=$("${PSQL[@]}" -tA -c "SELECT count(*) FROM pg_database WHERE datname='sakila_ext'")
if [ "$ext_exists" = "0" ]; then
  "${PSQL[@]}" -c "CREATE DATABASE sakila_ext OWNER \"$PG_USER\""
fi
# PSQL_EXT 已在文件头按 KM_PG_USER/KM_PG_PASSWORD 参数化定义——此处不得再
# 硬编码角色覆盖(参数化只做半截会让 ext 半段静默回退 kongming,非超管在
# 数据段 DISABLE TRIGGER ALL 必拒,与文件头文档化用法自相矛盾)
ext_tables=$("${PSQL_EXT[@]}" -tA -c \
  "SELECT count(*) FROM information_schema.tables
   WHERE table_schema='public' AND table_type IN ('BASE TABLE','VIEW')")
if [ "$ext_tables" -ge 21 ]; then
  echo "    sakila_ext 已有 $ext_tables 个对象,schema 段跳过"
else
  # 外部库原生落在 public schema:search_path 维持 dump 原样,仅剥超管专属语句
  # (OWNER/LANGUAGE/GRANT/REVOKE);TRIGGER ALL 同租户段口径只在数据段出现。
  # public schema 注释保留(独立业务库语义成立)
  sed -e '/OWNER TO postgres;/d' \
      -e '/PROCEDURAL LANGUAGE/d' \
      -e '/^GRANT /d' \
      -e '/^REVOKE /d' \
      "$DATA/sakila/postgres-sakila-schema.sql" | "${PSQL_EXT[@]}"
fi
ext_rows=$("${PSQL_EXT[@]}" -tA -c "SELECT count(*) FROM public.film")
if [ "$ext_rows" = "0" ]; then
  echo "==    sakila_ext 数据(231k 行 INSERT,约一两分钟)"
  # -1 单事务(与租户数据段同口径):中途失败全回滚,重跑幂等自愈
  "${PSQL_EXT[@]}" -1 -q < "$DATA/sakila/postgres-sakila-insert-data.sql"
else
  echo "    sakila_ext 数据已存在(film=$ext_rows 行),跳过"
fi
# 中文注释:上游 dump 无列注释,LLM schema 提示与 041 列标签均依赖它(独立库可重放)
echo "==    sakila_ext 中文注释"
"${PSQL_EXT[@]}" -f - < "$ROOT/infra/example-data/comments-ext.sql"
# 独立库只读授权(口径同 4.5):api 经动态源以 kongming 连入
"${PSQL_EXT[@]}" -c "GRANT USAGE ON SCHEMA public TO kongming"
"${PSQL_EXT[@]}" -c "GRANT SELECT ON ALL TABLES IN SCHEMA public TO kongming"

echo "完成。行数速览:"
"${PSQL[@]}" -tA -c "
SELECT 'sakila.' || relname || '=' || n_live_tup FROM pg_stat_user_tables
WHERE schemaname='tenant_sakila' ORDER BY 1;"
"${PSQL[@]}" -tA -c "
SELECT 'olist.' || relname || '=' || n_live_tup FROM pg_stat_user_tables
WHERE schemaname='tenant_olist' ORDER BY 1;"

echo "== 落点断言(防上游 dump 格式变化静默装错 schema)"
sakila_tables=$("${PSQL[@]}" -tA -c \
  "SELECT count(*) FROM information_schema.tables
   WHERE table_schema='tenant_sakila' AND table_type IN ('BASE TABLE','VIEW')")
public_leak=$("${PSQL[@]}" -tA -c \
  "SELECT count(*) FROM information_schema.tables
   WHERE table_schema='public' AND table_name IN ('film','rental','customer','actor')")
[ "$sakila_tables" -ge 21 ] || { echo "FAIL: tenant_sakila 仅 $sakila_tables 个对象"; exit 1; }
[ "$public_leak" = "0" ] || { echo "FAIL: sakila 表漏进 public($public_leak 张)"; exit 1; }
echo "OK: tenant_sakila $sakila_tables 个对象,public 零泄漏"
ext_tables=$("${PSQL_EXT[@]}" -tA -c \
  "SELECT count(*) FROM information_schema.tables
   WHERE table_schema='public' AND table_type IN ('BASE TABLE','VIEW')")
[ "$ext_tables" -ge 21 ] || { echo "FAIL: sakila_ext 仅 $ext_tables 个对象"; exit 1; }
echo "OK: sakila_ext $ext_tables 个对象(独立库)"
