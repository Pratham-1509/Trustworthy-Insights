-- Convert ID columns from TEXT to VARCHAR (TEXT can't be indexed directly),
-- then add primary keys and indexes so joins stay fast.
USE olist;

ALTER TABLE customers
  MODIFY customer_id VARCHAR(32) NOT NULL,
  MODIFY customer_unique_id VARCHAR(32),
  ADD PRIMARY KEY (customer_id),
  ADD INDEX idx_customers_unique (customer_unique_id);

ALTER TABLE orders
  MODIFY order_id VARCHAR(32) NOT NULL,
  MODIFY customer_id VARCHAR(32),
  MODIFY order_status VARCHAR(20),
  ADD PRIMARY KEY (order_id),
  ADD INDEX idx_orders_customer (customer_id);

ALTER TABLE order_items
  MODIFY order_id VARCHAR(32),
  MODIFY product_id VARCHAR(32),
  MODIFY seller_id VARCHAR(32),
  ADD INDEX idx_items_order (order_id),
  ADD INDEX idx_items_product (product_id),
  ADD INDEX idx_items_seller (seller_id);

ALTER TABLE order_payments
  MODIFY order_id VARCHAR(32),
  ADD INDEX idx_payments_order (order_id);

ALTER TABLE order_reviews
  MODIFY order_id VARCHAR(32),
  ADD INDEX idx_reviews_order (order_id);

ALTER TABLE products
  MODIFY product_id VARCHAR(32) NOT NULL,
  MODIFY product_category_name VARCHAR(100),
  ADD PRIMARY KEY (product_id),
  ADD INDEX idx_products_category (product_category_name);

ALTER TABLE sellers
  MODIFY seller_id VARCHAR(32) NOT NULL,
  ADD PRIMARY KEY (seller_id);

ALTER TABLE category_translation
  MODIFY product_category_name VARCHAR(100),
  ADD INDEX idx_translation_category (product_category_name);
