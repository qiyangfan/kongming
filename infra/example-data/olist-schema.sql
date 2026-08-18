-- olist 电商数据集建表(在 tenant_olist schema 下执行,表名用短名利于 LLM 提示)
-- 源:Kaggle Brazilian E-Commerce Public Dataset(9 CSV)
-- 纪律:主键只加在数据保证唯一的列上;order_payments/order_reviews/geolocation 无 PK(原数据有重复形态)

CREATE TABLE IF NOT EXISTS orders (
    order_id                        varchar(40) PRIMARY KEY,
    customer_id                     varchar(40) NOT NULL,
    order_status                    varchar(20),
    order_purchase_timestamp        timestamp,
    order_approved_at               timestamp,
    order_delivered_carrier_date    timestamp,
    order_delivered_customer_date   timestamp,
    order_estimated_delivery_date   timestamp
);

CREATE TABLE IF NOT EXISTS order_items (
    order_id            varchar(40) NOT NULL,
    order_item_id       integer NOT NULL,
    product_id          varchar(40),
    seller_id           varchar(40),
    shipping_limit_date timestamp,
    price               numeric(10,2),
    freight_value       numeric(10,2),
    PRIMARY KEY (order_id, order_item_id)
);

CREATE TABLE IF NOT EXISTS customers (
    customer_id              varchar(40) PRIMARY KEY,
    customer_unique_id       varchar(40),
    customer_zip_code_prefix varchar(10),
    customer_city            varchar(60),
    customer_state           varchar(2)
);

CREATE TABLE IF NOT EXISTS order_payments (
    order_id             varchar(40),
    payment_sequential   integer,
    payment_type         varchar(30),
    payment_installments integer,
    payment_value        numeric(10,2)
);

CREATE TABLE IF NOT EXISTS order_reviews (
    review_id              varchar(40),
    order_id               varchar(40),
    review_score           integer,
    review_comment_title   text,
    review_comment_message text,
    review_creation_date   timestamp,
    review_answer_timestamp timestamp
);

CREATE TABLE IF NOT EXISTS products (
    product_id               varchar(40) PRIMARY KEY,
    product_category_name    varchar(80),
    product_name_lenght      real,
    product_description_lenght real,
    product_photos_qty       real,
    product_weight_g         real,
    product_length_cm        real,
    product_height_cm        real,
    product_width_cm         real
);

CREATE TABLE IF NOT EXISTS sellers (
    seller_id              varchar(40) PRIMARY KEY,
    seller_zip_code_prefix varchar(10),
    seller_city            varchar(60),
    seller_state           varchar(2)
);

CREATE TABLE IF NOT EXISTS geolocation (
    geolocation_zip_code_prefix varchar(10),
    geolocation_lat             double precision,
    geolocation_lng             double precision,
    geolocation_city            varchar(80),
    geolocation_state           varchar(2)
);

CREATE TABLE IF NOT EXISTS category_translation (
    product_category_name         varchar(80) PRIMARY KEY,
    product_category_name_english varchar(80)
);

CREATE INDEX IF NOT EXISTS idx_order_items_product ON order_items (product_id);
CREATE INDEX IF NOT EXISTS idx_order_items_seller ON order_items (seller_id);
CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders (customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_purchase ON orders (order_purchase_timestamp);
CREATE INDEX IF NOT EXISTS idx_order_payments_order ON order_payments (order_id);
CREATE INDEX IF NOT EXISTS idx_order_reviews_order ON order_reviews (order_id);
