#!/usr/bin/env bash
# 示例数据集装载(sakila + olist)→ Kongming dev 库。
# 幂等:已装载则跳过数据(注释/租户注册可重放)。
# 用法:infra/example-data/load.sh(需 kongming-postgres-1 容器在跑)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DATA="$ROOT/.example-data"
CT="kongming-postgres-1"
PSQL=(podman exec -i "$CT" psql -U kongming -d kongming -v ON_ERROR_STOP=1 -q)

[ -d "$DATA/sakila" ] || { echo "缺 $DATA/sakila"; exit 1; }
[ -d "$DATA/olist" ] || { echo "缺 $DATA/olist"; exit 1; }

echo "== 1/5 sakila schema → tenant_sakila"
sakila_tables=$("${PSQL[@]}" -tA -c \
  "SELECT count(*) FROM information_schema.tables
   WHERE table_schema='tenant_sakila' AND table_type IN ('BASE TABLE','VIEW')")
if [ "$sakila_tables" -ge 21 ]; then
  echo "    tenant_sakila 已有 $sakila_tables 个对象,schema 段跳过"
else
  "${PSQL[@]}" -c "CREATE SCHEMA IF NOT EXISTS tenant_sakila"
  # 预处理:search_path 改租户 schema;去超管专属语句(OWNER/LANGUAGE/GRANT/REVOKE/public schema 注释)
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
  echo "== 2/5 sakila 数据(231k 行 INSERT,约一两分钟)"
  sed -e 's/SET search_path = public, pg_catalog;/SET search_path = tenant_sakila, pg_catalog;/' \
      "$DATA/sakila/postgres-sakila-insert-data.sql" | "${PSQL[@]}"
else
  echo "== 2/5 sakila 数据已存在(film=$sakila_rows 行),跳过"
fi

echo "== 3/5 olist 建表 + COPY"
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

echo "== 4/5 中文注释(两租户)"
"${PSQL[@]}" -f - < "$ROOT/infra/example-data/comments.sql"

echo "== 5/5 租户注册 + admin 绑定"
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
