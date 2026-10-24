<p align="center"><img src="dashboard/assets/logo.png" alt="Trustworthy Insights" width="420"></p>

# Trustworthy Insights

**Upload any CSV or Excel files and get profiled, charted insights, where every number in the AI-written summary is checked against the data.**

## What it is

Trustworthy Insights is a Streamlit app built for **any tabular dataset**, not one fixed file. Drop in one or several CSV/Excel files and it:

- profiles each table and works out what every column is,
- finds how the tables join,
- analyses trends, categories, outliers and correlations,
- writes insights in plain English, and
- checks that the AI never states a number the data doesn't support.

It grew out of a full SQL analysis of the Brazilian Olist e-commerce dataset (included below as a worked example), generalised so the same workflow runs on data it has never seen.

## Features

| Tab | What it does |
|---|---|
| **Overview** | Rows, columns, duplicate rows and missing values for every uploaded table, plus a column-by-column profile |
| **Column roles** | Detects each column as date, measure, category, id or text. You can correct the guesses in an editable table |
| **Auto analysis** | Data-quality checks, monthly trends with the biggest changes, category breakdowns with share and cumulative %, outliers (1.5 x IQR) and correlations |
| **Insights** | Rule-based bullets computed in code, plus an optional AI summary written by Google Gemini |
| **Relationships** | Finds likely join keys by comparing values in same-named columns across tables |
| **SQL** | Read-only SELECT/WITH queries across all uploaded tables (DuckDB), with CSV download |
| **Charts** | Bar, line, histogram and scatter charts with a choice of columns and aggregation |

Because the analyses are driven by detected **column roles** rather than hard-coded column names, the same code adapts to whatever files you upload.

## Why you can trust the insights

```mermaid
flowchart LR
    A[Uploaded files] --> B[Role detection]
    B --> C[Facts computed in pandas]
    C --> D[Rule-based insights]
    C --> E[Gemini writes a summary]
    E --> F[Every number checked against the facts]
```

- **The code does the maths.** The model only explains a small set of verified facts.
- **Raw rows are never sent.** The exact facts are shown in the app before anything leaves your machine.
- **Numbers are checked.** After the summary appears, any number that isn't in the facts is flagged for review.
- **Incomplete periods are excluded.** The first and last months are left out of growth figures because they are often partial.
- **The AI is optional.** The rule-based insights work with no key and no internet.

## Run it locally

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r dashboard/requirements.txt
streamlit run dashboard/app.py
```

Then upload your own files in the sidebar, or tick **Use the sample Olist data** to try it instantly.

**Optional, for the AI summary:** create a `.env` file next to `app.py` (it is git-ignored):

```
GEMINI_API_KEY=your_key_here
# GEMINI_MODEL=gemini-flash-latest
```

### Using your own data

1. Upload one or more `.csv` or `.xlsx` files (an Excel file becomes one table per sheet).
2. Open **Column roles** and fix any wrong guesses, such as a numeric code detected as a measure.
3. Open **Auto analysis** and **Insights**, choosing the date column, measure and grouping you care about.
4. Use **Relationships** to see which tables can be joined, and **SQL** to query them.

---

## Worked example: Olist e-commerce analysis

The demo dataset is the [Brazilian E-Commerce Public Dataset by Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce) (about 100,000 orders, 2016 to 2018). Before building the app I analysed it in MySQL 8 to answer eight business questions (queries are in `sql/`):

1. How have monthly orders and revenue changed over time?
2. Which product categories bring in the most revenue?
3. Which states generate the most revenue?
4. How many orders are delivered late, and how does that vary by state?
5. Does late delivery lower review scores?
6. Which payment methods are used most?
7. How many customers buy more than once?
8. How concentrated is seller revenue, and which top sellers have weak reviews or late deliveries?

### Key findings

- **Growth came from order volume and flattened in 2018.** Monthly orders grew from 797 (Jan 2017) to 6,428 (Aug 2018), about 8x, peaking at 7,507 in Nov 2017. Order value stayed around R$155 to R$175. The first and last months are incomplete and were excluded.
- **Revenue is spread across categories.** The top category, health_beauty, is 9.3% of revenue and the top 10 reach about 62%. Revenue per order ranges from R$77 (telephony) to R$215 (office_furniture).
- **Revenue is concentrated geographically.** Sao Paulo alone is 38.27% of product revenue, and SP, RJ and MG together are about 63%.
- **Delivery estimates are padded.** Orders were promised in 24.4 days on average and arrived in 12.5. Only 8.11% were late, but the rate reaches 19.67% in MA. Distance matters (15.0 days across states vs 7.9 within a state), and so does how tight the promise is (RJ is next to SP yet 13.47% late).
- **Late delivery goes with much lower reviews.** On-time orders average 4.29 stars and late orders 2.57. This is a correlation, not proof of cause.
- **Credit cards dominate payments** at 78.44% of payment value, with 3.5 installments on average.
- **Repeat purchasing is rare.** 96.96% of customers ordered once (the data spans about two years, so this is probably understated).

### Data-quality notes

- Date columns loaded as text and were converted to `DATETIME`.
- Revenue excludes canceled orders. Product revenue uses item price (no shipping); payment value includes shipping.
- Seller-level review and late-rate figures are per order item, so multi-item orders count more than once.

### Reproduce the SQL analysis

1. Download the dataset from Kaggle into `data/`.
2. Create a MySQL database named `olist`.
3. Set `DATABASE_URL` (for example `mysql+pymysql://user:password@localhost:3306/olist`) and run `python load_data.py`.
4. Run the scripts in `sql/` in numbered order.

The sample in `dashboard/sample_data/` is a random 5,000-order slice, so its figures differ from the full-data numbers above.

## Tech stack

Python, pandas, NumPy, Streamlit, Plotly, DuckDB, MySQL 8, SQLAlchemy, Google Gemini API (`google-genai`).

## Project structure

```
dashboard/
  app.py                Streamlit app
  requirements.txt
  sample_data/          small demo dataset (Olist sample)
make_sample_data.py     builds sample_data/ from the full files
```

## Limitations

- Role detection is a heuristic. A column called `value` can't be understood from its name, so the roles table is editable.
- The app can't tell whether summing a measure makes sense (for example, scores). Use "mean" or "count rows" there.
- Very messy files (mixed date formats, currency symbols in numbers, multi-row headers) may need cleaning first.
- The SQL box allows one SELECT/WITH statement. That is fine locally but is not a full security boundary.
- Insights describe patterns in the data you upload. They are not causal claims, and the AI can still over-explain, which is why its numbers are checked and its text should be read against the facts.

## Data and credits

Demo data: [Brazilian E-Commerce Public Dataset by Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce) on Kaggle. Credit Olist, and check the dataset's license on that page before redistributing the sample files.

## Author

Prathamesh, 4th-year engineering student.
GitHub: [Pratham-1509](https://github.com/Pratham-1509)
