"""Load the Olist CSV files into a PostgreSQL database.

Setup:
    pip install pandas sqlalchemy psycopg2-binary
    set DATABASE_URL (never hard-code your password in this file):
      Windows (PowerShell): $env:DATABASE_URL="postgresql+psycopg2://postgres:YOUR_PASSWORD@localhost:5432/olist"
      Mac/Linux:            export DATABASE_URL="postgresql+psycopg2://postgres:YOUR_PASSWORD@localhost:5432/olist"
Run:
    python load_data.py
"""
import os
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine

engine = create_engine(os.environ["DATABASE_URL"])
DATA_DIR = Path("data")

# table name -> CSV file name (check these match the files you downloaded)
TABLES = {
    "customers": "olist_customers_dataset.csv",
    "orders": "olist_orders_dataset.csv",
    "order_items": "olist_order_items_dataset.csv",
    "order_payments": "olist_order_payments_dataset.csv",
    "order_reviews": "olist_order_reviews_dataset.csv",
    "products": "olist_products_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
    "category_translation": "product_category_name_translation.csv",
}

for table, filename in TABLES.items():
    df = pd.read_csv(DATA_DIR / filename)
    df.to_sql(table, engine, if_exists="replace", index=False)
    print(f"Loaded {table}: {len(df):,} rows")

print("Done. Open pgAdmin and check the tables.")
