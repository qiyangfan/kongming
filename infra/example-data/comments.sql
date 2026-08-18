-- 示例数据集中文注释(经 /internal/meta/schema 进入 LLM schema 提示,问答质量关键)
-- 覆盖:LLM 生成最常用到的表/列;未注释列不影响正确性

-- ============ tenant_sakila(影视租赁) ============
COMMENT ON TABLE tenant_sakila.actor IS '演员';
COMMENT ON TABLE tenant_sakila.film IS '影片';
COMMENT ON TABLE tenant_sakila.category IS '影片分类';
COMMENT ON TABLE tenant_sakila.film_category IS '影片-分类关联';
COMMENT ON TABLE tenant_sakila.film_actor IS '影片-演员关联';
COMMENT ON TABLE tenant_sakila.customer IS '客户';
COMMENT ON TABLE tenant_sakila.address IS '地址';
COMMENT ON TABLE tenant_sakila.city IS '城市';
COMMENT ON TABLE tenant_sakila.country IS '国家';
COMMENT ON TABLE tenant_sakila.store IS '门店';
COMMENT ON TABLE tenant_sakila.staff IS '员工';
COMMENT ON TABLE tenant_sakila.rental IS '租赁单(一次租借一行)';
COMMENT ON TABLE tenant_sakila.payment IS '支付单(按租赁单收款)';
COMMENT ON TABLE tenant_sakila.inventory IS '影片库存(按门店)';
COMMENT ON TABLE tenant_sakila.language IS '语言';

COMMENT ON COLUMN tenant_sakila.film.title IS '片名';
COMMENT ON COLUMN tenant_sakila.film.rental_rate IS '租金(单价)';
COMMENT ON COLUMN tenant_sakila.film.length IS '时长(分钟)';
COMMENT ON COLUMN tenant_sakila.film.rating IS '分级(G/PG/PG-13/R/NC-17)';
COMMENT ON COLUMN tenant_sakila.film.release_year IS '发行年份';
COMMENT ON COLUMN tenant_sakila.rental.rental_date IS '租借时间';
COMMENT ON COLUMN tenant_sakila.rental.return_date IS '归还时间(未还为空)';
COMMENT ON COLUMN tenant_sakila.payment.amount IS '支付金额';
COMMENT ON COLUMN tenant_sakila.payment.payment_date IS '支付时间';
COMMENT ON COLUMN tenant_sakila.customer.first_name IS '名';
COMMENT ON COLUMN tenant_sakila.customer.last_name IS '姓';
COMMENT ON COLUMN tenant_sakila.customer.email IS '邮箱';
COMMENT ON COLUMN tenant_sakila.customer.active IS '是否活跃(1/0)';
COMMENT ON COLUMN tenant_sakila.customer.create_date IS '注册日期';
COMMENT ON COLUMN tenant_sakila.category.name IS '分类名';
COMMENT ON COLUMN tenant_sakila.actor.first_name IS '名';
COMMENT ON COLUMN tenant_sakila.actor.last_name IS '姓';
COMMENT ON COLUMN tenant_sakila.store.store_id IS '门店编号';
COMMENT ON COLUMN tenant_sakila.staff.first_name IS '名';
COMMENT ON COLUMN tenant_sakila.staff.last_name IS '姓';

-- ============ tenant_olist(巴西电商) ============
COMMENT ON TABLE tenant_olist.orders IS '订单';
COMMENT ON TABLE tenant_olist.order_items IS '订单明细(一单一至多行)';
COMMENT ON TABLE tenant_olist.customers IS '客户';
COMMENT ON TABLE tenant_olist.products IS '商品';
COMMENT ON TABLE tenant_olist.sellers IS '卖家';
COMMENT ON TABLE tenant_olist.order_payments IS '订单支付(一单可多支付方式)';
COMMENT ON TABLE tenant_olist.order_reviews IS '订单评价';
COMMENT ON TABLE tenant_olist.geolocation IS '邮编地理坐标';
COMMENT ON TABLE tenant_olist.category_translation IS '品类名英译(葡→英)';

COMMENT ON COLUMN tenant_olist.orders.order_status IS '订单状态(delivered/shipped/canceled 等)';
COMMENT ON COLUMN tenant_olist.orders.order_purchase_timestamp IS '下单时间';
COMMENT ON COLUMN tenant_olist.orders.order_approved_at IS '支付核准时间';
COMMENT ON COLUMN tenant_olist.orders.order_delivered_customer_date IS '实际送达时间';
COMMENT ON COLUMN tenant_olist.orders.order_estimated_delivery_date IS '预计送达时间';
COMMENT ON COLUMN tenant_olist.order_items.price IS '商品售价';
COMMENT ON COLUMN tenant_olist.order_items.freight_value IS '运费';
COMMENT ON COLUMN tenant_olist.order_payments.payment_type IS '支付方式(credit_card/boleto/voucher/debit_card)';
COMMENT ON COLUMN tenant_olist.order_payments.payment_value IS '支付金额';
COMMENT ON COLUMN tenant_olist.order_payments.payment_installments IS '分期数';
COMMENT ON COLUMN tenant_olist.order_reviews.review_score IS '评分(1-5)';
COMMENT ON COLUMN tenant_olist.order_reviews.review_comment_message IS '评价内容';
COMMENT ON COLUMN tenant_olist.customers.customer_city IS '客户城市';
COMMENT ON COLUMN tenant_olist.customers.customer_state IS '客户州(二字码)';
COMMENT ON COLUMN tenant_olist.customers.customer_unique_id IS '客户自然人标识(customer_id 每单一新)';
COMMENT ON COLUMN tenant_olist.products.product_category_name IS '品类(葡语)';
COMMENT ON COLUMN tenant_olist.products.product_weight_g IS '重量(克)';
COMMENT ON COLUMN tenant_olist.sellers.seller_city IS '卖家城市';
COMMENT ON COLUMN tenant_olist.sellers.seller_state IS '卖家州(二字码)';
