-- sakila_ext(独立业务库)中文注释——与 comments.sql 的 tenant_sakila 段同款,
-- 仅 schema 前缀不同(外部库原生落 public)。**只能对 sakila_ext 库执行**
-- (load.sh 第 6 步),严禁混入 comments.sql 在 kongming 库执行——
-- public 前缀会误伤 kongming 库自身业务表。
-- 通过 /internal/meta/schema 提供给模型查询提示，并用于页面列名显示。

COMMENT ON TABLE public.actor IS '演员';
COMMENT ON TABLE public.film IS '影片';
COMMENT ON TABLE public.category IS '影片分类';
COMMENT ON TABLE public.film_category IS '影片-分类关联';
COMMENT ON TABLE public.film_actor IS '影片-演员关联';
COMMENT ON TABLE public.customer IS '客户';
COMMENT ON TABLE public.address IS '地址';
COMMENT ON TABLE public.city IS '城市';
COMMENT ON TABLE public.country IS '国家';
COMMENT ON TABLE public.store IS '门店';
COMMENT ON TABLE public.staff IS '员工';
COMMENT ON TABLE public.rental IS '租赁单(一次租借一行)';
COMMENT ON TABLE public.payment IS '支付单(按租赁单收款)';
COMMENT ON TABLE public.inventory IS '影片库存(按门店)';
COMMENT ON TABLE public.language IS '语言';

COMMENT ON COLUMN public.film.title IS '片名';
COMMENT ON COLUMN public.film.rental_rate IS '租金(单价)';
COMMENT ON COLUMN public.film.length IS '时长(分钟)';
COMMENT ON COLUMN public.film.rating IS '分级(G/PG/PG-13/R/NC-17)';
COMMENT ON COLUMN public.film.release_year IS '发行年份';
COMMENT ON COLUMN public.rental.rental_date IS '租借时间';
COMMENT ON COLUMN public.rental.return_date IS '归还时间(未还为空)';
COMMENT ON COLUMN public.payment.amount IS '支付金额';
COMMENT ON COLUMN public.payment.payment_date IS '支付时间';
COMMENT ON COLUMN public.customer.first_name IS '名';
COMMENT ON COLUMN public.customer.last_name IS '姓';
COMMENT ON COLUMN public.customer.email IS '邮箱';
COMMENT ON COLUMN public.customer.active IS '是否活跃(1/0)';
COMMENT ON COLUMN public.customer.create_date IS '注册日期';
COMMENT ON COLUMN public.category.name IS '分类名';
COMMENT ON COLUMN public.actor.first_name IS '名';
COMMENT ON COLUMN public.actor.last_name IS '姓';
COMMENT ON COLUMN public.store.store_id IS '门店编号';
COMMENT ON COLUMN public.staff.first_name IS '名';
COMMENT ON COLUMN public.staff.last_name IS '姓';
