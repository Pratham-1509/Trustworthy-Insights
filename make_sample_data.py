"""Build a small, consistent sample of the Olist dataset for the demo app.

Run from the project folder (the one that contains data/):
    python make_sample_data.py

Reads  data/*.csv         (the full Olist files you downloaded from Kaggle)
Writes sample_data/*.csv  (a few MB, safe to commit to GitHub)

The sample is a random set of orders plus every row linked to them, so the
tables still join correctly. Figures from the sample will NOT match your
full-data SQL results (it is only a small slice), so say that in the README.
"""
from pathlib import Path

import pandas as pd

N_ORDERS = 5000
SEED = 42

SRC = Path("data")
DST = Path("sample_data")
DST.mkdir(exist_ok=True)

FILES = {
    "orders": "olist_orders_dataset.csv",
    "customers": "olist_customers_dataset.csv",
    "items": "olist_order_items_dataset.csv",
    "payments": "olist_order_payments_dataset.csv",
    "reviews": "olist_order_reviews_dataset.csv",
    "products": "olist_products_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
    "translation": "product_category_name_translation.csv",
}


def read(key: str) -> pd.DataFrame:
    return pd.read_csv(SRC / FILES[key])


orders = read("orders").sample(N_ORDERS, random_state=SEED)
order_ids = set(orders["order_id"])

customers = read("customers")
customers = customers[customers["customer_id"].isin(orders["customer_id"])]

items = read("items")
items = items[items["order_id"].isin(order_ids)]

payments = read("payments")
payments = payments[payments["order_id"].isin(order_ids)]

reviews = read("reviews")
reviews = reviews[reviews["order_id"].isin(order_ids)]

products = read("products")
products = products[products["product_id"].isin(items["product_id"])]

sellers = read("sellers")
sellers = sellers[sellers["seller_id"].isin(items["seller_id"])]

translation = read("translation")  # tiny lookup table, keep it whole

result = {
    "orders": orders,
    "customers": customers,
    "items": items,
    "payments": payments,
    "reviews": reviews,
    "products": products,
    "sellers": sellers,
    "translation": translation,
}

total_bytes = 0
for key, df in result.items():
    out = DST / FILES[key]
    df.to_csv(out, index=False)
    total_bytes += out.stat().st_size
    print(f"{out.name}: {len(df):,} rows")

print(f"\nDone. Total size: {total_bytes / 1_000_000:.1f} MB in {DST}/")
