"""Multi-File Data Explorer (Phase 2 starter).

Upload several CSV / Excel files, then:
  - profile every table (rows, missing values, duplicates, column types)
  - detect likely join keys between tables
  - run read-only SQL across all tables (DuckDB)
  - build quick charts
  - generate an AI summary with Google Gemini (key read from .env)

Setup:
    pip install streamlit pandas numpy plotly duckdb openpyxl python-dotenv google-genai

Create a file named .env in the same folder as app.py:
    GEMINI_API_KEY=your_key_here
    # optional:
    # GEMINI_MODEL=gemini-flash-latest

Run:
    streamlit run app.py
"""
import hashlib
import json
import os
import re
import warnings
from io import BytesIO
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv

load_dotenv(override=True)  # Load GEMINI_API_KEY from the .env file in this project folder.

ASSETS = Path(__file__).parent / "assets"
ICON = ASSETS / "icon.png"
LOGO = ASSETS / "logo.png"

st.set_page_config(
    page_title="Trustworthy Insights",
    page_icon=str(ICON) if ICON.exists() else "📊",
    layout="wide",
)
if LOGO.exists():
    st.logo(str(LOGO), size="large", icon_image=str(ICON) if ICON.exists() else None)
    st.markdown(
        """
        <style>
        img[data-testid="stLogo"], [alt=Logo] {
            height: 3.1rem;
            max-width: 100%;
            object-fit: contain;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
st.title("Trustworthy Insights")
st.caption("Upload any CSV or Excel files. Profile them, find join keys, run SQL, chart the results, and get insights where every number is checked.")

# ---------- helpers ----------
def clean_name(text: str) -> str:
    """Turn a file or sheet name into a safe SQL table name."""
    name = re.sub(r"\W+", "_", text).strip("_").lower()
    if not name or name[0].isdigit():
        name = "t_" + name
    return name


@st.cache_data(show_spinner=False)
def read_file(filename: str, data: bytes) -> dict:
    """Return {table_name: DataFrame}. An Excel file gives one table per sheet."""
    stem = filename.rsplit(".", 1)[0]
    if filename.lower().endswith(".csv"):
        try:
            df = pd.read_csv(BytesIO(data))
        except UnicodeDecodeError:
            df = pd.read_csv(BytesIO(data), encoding="latin-1")
        return {clean_name(stem): df}
    sheets = pd.read_excel(BytesIO(data), sheet_name=None)
    return {clean_name(f"{stem}_{sheet}"): df for sheet, df in sheets.items()}


def profile(df: pd.DataFrame) -> pd.DataFrame:
    """One row per column: type, missing count and %, number of unique values."""
    return pd.DataFrame({
        "column": df.columns,
        "type": [str(t) for t in df.dtypes],
        "missing": df.isna().sum().values,
        "missing_%": (df.isna().mean() * 100).round(2).values,
        "unique": df.nunique().values,
    })


def find_join_candidates(tables: dict, sample: int = 50000) -> pd.DataFrame:
    """Columns with the same name in two tables, scored by how many values overlap."""
    rows = []
    names = list(tables)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            for col in set(tables[a].columns) & set(tables[b].columns):
                va = set(tables[a][col].dropna().astype(str).head(sample))
                vb = set(tables[b][col].dropna().astype(str).head(sample))
                if not va or not vb:
                    continue
                rows.append({
                    "table_a": a,
                    "table_b": b,
                    "column": col,
                    "overlap_%": round(100 * len(va & vb) / min(len(va), len(vb)), 1),
                })
    if not rows:
        return pd.DataFrame(columns=["table_a", "table_b", "column", "overlap_%"])
    return pd.DataFrame(rows).sort_values("overlap_%", ascending=False)


def is_safe_query(sql: str) -> bool:
    """Allow a single SELECT / WITH statement only."""
    s = sql.strip().rstrip(";").strip()
    return ";" not in s and re.match(r"(?is)^(select|with)\b", s) is not None


# ---------- column roles ----------
ROLE_OPTIONS = ["date", "measure", "category", "id", "text", "ignore"]
ID_NAME = re.compile(r"(^|_)(id|key|uuid|zip|code|prefix)(_|$)", re.I)
DATE_LIKE = re.compile(r"^\s*\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}")


def detect_roles(df: pd.DataFrame) -> pd.DataFrame:
    """Guess what each column is: date, measure, category, id or text."""
    rows = []
    n = len(df)
    for col in df.columns:
        s = df[col]
        nn = s.dropna()
        unique = int(nn.nunique())
        name_is_id = bool(ID_NAME.search(str(col)))
        if nn.empty:
            role = "ignore"
        elif pd.api.types.is_datetime64_any_dtype(s):
            role = "date"
        elif pd.api.types.is_bool_dtype(s):
            role = "category"
        elif pd.api.types.is_numeric_dtype(s):
            if name_is_id:
                role = "id"
            elif unique <= 10:
                role = "category"
            else:
                role = "measure"
        else:
            sample = nn.astype(str).head(200)
            if len(sample):
                date_share = sample.str.match(DATE_LIKE).mean()
                has_spaces = sample.str.contains(" ").mean()
                avg_len = sample.str.len().mean()
            else:
                date_share, has_spaces, avg_len = 0, 0, 0
            ratio = unique / max(len(nn), 1)
            if date_share >= 0.9:
                role = "date"
            elif name_is_id or (ratio > 0.9 and has_spaces < 0.2 and avg_len <= 40):
                role = "id"
            elif unique <= max(50, 0.05 * n):
                role = "category"
            else:
                role = "text"
        rows.append({
            "column": col,
            "role": role,
            "type": str(s.dtype),
            "unique": unique,
            "missing_%": round(s.isna().mean() * 100, 2),
        })
    return pd.DataFrame(rows)


def apply_roles(df: pd.DataFrame, roles: pd.DataFrame) -> pd.DataFrame:
    """Return a copy where date columns are real dates and measures are numbers."""
    out = df.copy()
    for _, r in roles.iterrows():
        col, role = r["column"], r["role"]
        if col not in out.columns:
            continue
        if role == "date" and not pd.api.types.is_datetime64_any_dtype(out[col]):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                out[col] = pd.to_datetime(out[col], errors="coerce")
        elif role == "measure" and not pd.api.types.is_numeric_dtype(out[col]):
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def roles_key(name: str, df: pd.DataFrame) -> str:
    return f"{name}|{df.shape}|{'|'.join(map(str, df.columns))}"


def current_roles(name: str, df: pd.DataFrame) -> pd.DataFrame:
    """The user's edited roles if they made any, otherwise the detected ones."""
    k = roles_key(name, df)
    if f"detected::{k}" not in st.session_state:
        st.session_state[f"detected::{k}"] = detect_roles(df)
    return st.session_state.get(f"edited::{k}", st.session_state[f"detected::{k}"])


# ---------- insights ----------
MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")

SYSTEM_PROMPT = (
    "You are a careful data analyst. "
    "Use only the verified facts provided. "
    "Write 4 to 6 concise bullet points and 2 questions "
    "worth investigating. "
    "Do not invent numbers, causes, or outside context. "
    "Mention relevant data-quality caveats."
)


def human(x) -> str:
    """Format a number for reading: 1,187,224 -> '1.19M', 142.6931 -> '142.69'."""
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "n/a"
    ax = abs(x)
    if ax >= 1e6:
        return f"{x / 1e6:,.2f}M"
    if ax >= 1e3 or float(x).is_integer():
        return f"{x:,.0f}"
    return f"{x:,.2f}"


def month_label(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m")


def build_facts(name, raw, typed, roles, date_col, measure, cat_col, agg):
    """Compute a small set of verified facts, plus plain-English bullets from them."""
    role_of = dict(zip(roles["column"], roles["role"]))
    all_measures = [c for c, r in role_of.items() if r == "measure"]
    label = "row count" if measure is None else f"{agg} of {measure}"
    facts = {
        "table": name,
        "rows": f"{len(raw):,}",
        "columns": int(raw.shape[1]),
        "column_roles": {
            r: [c for c, rr in role_of.items() if rr == r]
            for r in ROLE_OPTIONS if r != "ignore"
        },
    }
    bullets = []

    # data quality
    issues = []
    dup = int(raw.duplicated().sum())
    if dup:
        issues.append(f"{dup:,} duplicate rows")
    for c in raw.columns:
        miss = raw[c].isna().mean() * 100
        if miss >= 20:
            issues.append(f"{c} is {miss:.0f}% missing")
    facts["data_quality_issues"] = issues or ["none found"]
    quality = ("Data-quality flags: " + "; ".join(issues) + ".") if issues else (
        "No duplicate rows or mostly-empty columns."
    )
    bullets.append(f"{len(raw):,} rows and {raw.shape[1]} columns. {quality}")

    # trend over time (monthly)
    if date_col:
        ts = typed.dropna(subset=[date_col])
        if not ts.empty:
            per = ts[date_col].dt.to_period("M").dt.to_timestamp()
            if measure is None:
                series = ts.groupby(per).size()
            else:
                series = ts.groupby(per)[measure].agg(agg)
            series = series.sort_index()
            if len(series) >= 3:
                inner = series.iloc[1:-1] if len(series) >= 4 else series
                first_p, last_p = inner.index[0], inner.index[-1]
                v0, v1 = inner.iloc[0], inner.iloc[-1]
                growth = (v1 / v0 - 1) * 100 if v0 else np.nan
                peak_p, low_p = inner.idxmax(), inner.idxmin()
                pc = (inner.pct_change() * 100).replace([np.inf, -np.inf], np.nan).dropna()
                trend = {
                    "metric": f"{label} by month ({date_col})",
                    "months_in_data": len(series),
                    "first_month_in_data": f"{month_label(series.index[0])} ({human(series.iloc[0])})",
                    "last_month_in_data": f"{month_label(series.index[-1])} ({human(series.iloc[-1])})",
                    "compared_months": f"{month_label(first_p)} to {month_label(last_p)}",
                    "change_over_compared_months": "n/a" if np.isnan(growth) else f"{growth:+.1f}%",
                    "peak_month": f"{month_label(peak_p)} ({human(inner.max())})",
                    "lowest_month": f"{month_label(low_p)} ({human(inner.min())})",
                }
                if len(series) >= 4:
                    trend["note"] = (
                        "The first and last months in the data may be incomplete, so the "
                        "comparison, peak and lowest exclude them."
                    )
                if not pc.empty:
                    trend["largest_monthly_rise"] = f"{pc.max():+.1f}% in {month_label(pc.idxmax())}"
                    trend["largest_monthly_fall"] = f"{pc.min():+.1f}% in {month_label(pc.idxmin())}"
                facts["trend"] = trend
                change = "" if np.isnan(growth) else f" ({growth:+.1f}%)"
                bullets.append(
                    f"The {label} went from {human(v0)} in {month_label(first_p)} to "
                    f"{human(v1)} in {month_label(last_p)}{change}."
                )
                bullets.append(
                    f"Peak month: {month_label(peak_p)} ({human(inner.max())}); lowest month: "
                    f"{month_label(low_p)} ({human(inner.min())})."
                )
                if not pc.empty:
                    bullets.append(
                        f"Largest monthly rise: {pc.max():+.1f}% in {month_label(pc.idxmax())}; "
                        f"largest fall: {pc.min():+.1f}% in {month_label(pc.idxmin())}."
                    )
                if len(series) >= 4:
                    bullets.append(
                        "The first and last months were left out of these figures because "
                        "they may be incomplete."
                    )
            else:
                bullets.append("Fewer than 3 months of data, so no monthly trend was calculated.")

    # category breakdown
    if cat_col:
        grp = typed.groupby(cat_col, dropna=False)
        counts = grp.size()
        g = counts if measure is None else grp[measure].agg(agg)
        g = g.sort_values(ascending=False)
        if agg == "sum" or measure is None:
            shares = 100 * g / g.sum()
            top3 = float(shares.head(3).sum())
            facts["category_breakdown"] = {
                "grouped_by": cat_col,
                "metric": label,
                "number_of_groups": int(len(g)),
                "top_groups": [f"{k}: {human(v)} ({s:.1f}%)" for (k, v), s in zip(g.head(5).items(), shares.head(5))],
                "top_3_share": f"{top3:.1f}%",
            }
            bullets.append(
                f"By {cat_col}, '{g.index[0]}' leads with {shares.iloc[0]:.1f}% of the {label}; "
                f"the top 3 of {len(g)} groups account for {top3:.1f}%."
            )
        else:
            big = counts[counts >= 30].index
            gm = (g[g.index.isin(big)] if len(big) else g).sort_values(ascending=False)
            facts["category_breakdown"] = {
                "grouped_by": cat_col,
                "metric": label,
                "note": "Groups with fewer than 30 rows are ignored when ranking, if any group has 30 or more.",
                "highest": [f"{k}: {human(v)} (n={int(counts[k]):,})" for k, v in gm.head(3).items()],
                "lowest": [f"{k}: {human(v)} (n={int(counts[k]):,})" for k, v in gm.tail(3).items()],
            }
            bullets.append(
                f"By {cat_col}, the highest {label} is '{gm.index[0]}' ({human(gm.iloc[0])}) and the "
                f"lowest is '{gm.index[-1]}' ({human(gm.iloc[-1])})."
            )

    # outliers
    out_rows = []
    for mm in all_measures[:15]:
        s = typed[mm].dropna()
        if len(s) < 10:
            continue
        q1, q3 = s.quantile([0.25, 0.75])
        iqr = q3 - q1
        n_out = int(((s < q1 - 1.5 * iqr) | (s > q3 + 1.5 * iqr)).sum())
        if n_out:
            out_rows.append((mm, 100 * n_out / len(s)))
    out_rows.sort(key=lambda x: -x[1])
    if out_rows:
        facts["outliers"] = [f"{m}: {p:.1f}% of values are beyond 1.5 x IQR" for m, p in out_rows[:3]]
        bullets.append(
            f"'{out_rows[0][0]}' has the most unusual values ({out_rows[0][1]:.1f}% beyond 1.5 x IQR); "
            "unusual does not mean wrong."
        )

    # correlation
    if len(all_measures) >= 2:
        corr = typed[all_measures[:15]].corr(numeric_only=True)
        pairs = corr.where(np.triu(np.ones(corr.shape, dtype=bool), k=1)).stack()
        if not pairs.empty:
            idx = pairs.abs().idxmax()
            r = float(pairs.loc[idx])
            strength = "weak" if abs(r) < 0.3 else "moderate" if abs(r) < 0.6 else "strong"
            facts["strongest_measure_pair"] = f"{idx[0]} vs {idx[1]}: {r:.2f} ({strength})"
            bullets.append(
                f"The strongest relationship between measures is '{idx[0]}' and '{idx[1]}' "
                f"(correlation {r:.2f}, {strength}). Correlation does not prove cause."
            )
    return facts, bullets


def get_api_key():
    """Load the Gemini key from .env (preferred) or Streamlit secrets."""
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

    if not key:
        try:
            key = st.secrets.get("GEMINI_API_KEY")
        except Exception:
            key = None

    if isinstance(key, str):
        key = key.strip().strip('"').strip("'")

    return key or None


def generate_ai_summary(facts: dict) -> str:
    """Generate an AI summary using Google Gemini. The key is read from .env."""
    from google import genai
    from google.genai import errors, types

    key = get_api_key()
    if not key:
        raise ValueError(
            "GEMINI_API_KEY not found. Add it to the .env file next to app.py "
            "(GEMINI_API_KEY=your_key) and restart the app."
        )

    client = genai.Client(api_key=key)
    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=(
                "Analyze these verified facts. Do not invent data.\n\n"
                + json.dumps(facts, indent=2, default=str)
            ),
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                # Newer Gemini models spend some of this budget on internal
                # "thinking", so keep it generous to avoid empty/cut-off answers.
                max_output_tokens=4096,
                temperature=0.2,
            ),
        )

        text = (response.text or "").strip()
        if not text:
            raise RuntimeError("Gemini returned an empty response. Please try again.")
        return text

    except errors.APIError as exc:
        code = getattr(exc, "code", None)

        if code in (400, 401):
            raise RuntimeError(
                "Gemini rejected the API key or request. Check that GEMINI_API_KEY "
                "in your .env file is a valid Google Gemini key "
                "(get one at https://aistudio.google.com/apikey)."
            ) from exc

        if code == 403:
            raise RuntimeError(
                "Access denied. Check your Gemini API key permissions and restrictions."
            ) from exc

        if code == 404:
            raise RuntimeError(
                f"Model '{MODEL}' is unavailable. Set GEMINI_MODEL in .env to a model "
                "supported by your API key."
            ) from exc

        if code == 429:
            raise RuntimeError(
                "Gemini quota or rate limit reached. Please try again later."
            ) from exc

        raise RuntimeError(f"Gemini API error: {exc}") from exc


def numbers_to_check(summary: str, facts: dict) -> list:
    """Numbers in the AI text that do not appear anywhere in the facts."""
    facts_text = json.dumps(facts, default=str)
    found = re.findall(r"\d[\d,]*(?:\.\d+)?", summary)
    out = set()
    for n in found:
        n = n.rstrip(",.")
        if len(n.replace(",", "").replace(".", "")) >= 3 and n not in facts_text:
            out.add(n)
    return sorted(out)


# ---------- upload ----------
files = st.sidebar.file_uploader(
    "Upload CSV or Excel files", type=["csv", "xlsx"], accept_multiple_files=True
)
SAMPLE_DIR = Path(__file__).parent / "sample_data"
use_sample = st.sidebar.checkbox(
    "Use the sample Olist data", value=False, disabled=not SAMPLE_DIR.exists()
)
if not files and not use_sample:
    st.info("Upload one or more files in the sidebar, or tick 'Use the sample Olist data'.")
    st.stop()

tables = {}
if use_sample:
    for p in sorted(SAMPLE_DIR.glob("*.csv")):
        tables[clean_name(p.stem)] = pd.read_csv(p)
for f in files:
    for name, df in read_file(f.name, f.getvalue()).items():
        base, n = name, 2
        while name in tables:  # two files with the same name
            name, n = f"{base}_{n}", n + 1
        tables[name] = df

# DuckDB lets us run SQL directly on the DataFrames
con = duckdb.connect()
for name, df in tables.items():
    con.register(name, df)

st.sidebar.success(f"{len(tables)} table(s) loaded")
st.sidebar.write(", ".join(tables))

tab_overview, tab_roles, tab_auto, tab_insights, tab_rel, tab_sql, tab_chart = st.tabs(
    ["Overview", "Column roles", "Auto analysis", "Insights", "Relationships", "SQL", "Charts"]
)

# ---------- overview ----------
with tab_overview:
    summary = pd.DataFrame([
        {
            "table": n,
            "rows": len(d),
            "columns": d.shape[1],
            "duplicate_rows": int(d.duplicated().sum()),
            "missing_cells_%": round(d.isna().mean().mean() * 100, 2),
        }
        for n, d in tables.items()
    ])
    st.subheader("Tables")
    st.dataframe(summary, hide_index=True)

    t = st.selectbox("Inspect a table", list(tables), key="overview_table")
    st.subheader(f"Columns in {t}")
    st.dataframe(profile(tables[t]), hide_index=True)
    st.subheader("First 20 rows")
    st.dataframe(tables[t].head(20))

# ---------- column roles ----------
with tab_roles:
    st.write(
        "The app guesses what each column is. Fix any wrong guesses in the **role** "
        "column; the Auto analysis tab uses your corrections."
    )
    t = st.selectbox("Table", list(tables), key="roles_table")
    df = tables[t]
    k = roles_key(t, df)
    current_roles(t, df)  # makes sure detection has run
    edited = st.data_editor(
        st.session_state[f"detected::{k}"],
        hide_index=True,
        key=f"editor::{k}",
        disabled=["column", "type", "unique", "missing_%"],
        column_config={
            "role": st.column_config.SelectboxColumn(
                "role", options=ROLE_OPTIONS, required=True
            )
        },
    )
    st.session_state[f"edited::{k}"] = edited
    st.caption(
        "date = a time column, measure = a number to sum or average, category = a "
        "label to group by, id = an identifier, text = free text, ignore = skip it."
    )

# ---------- auto analysis ----------
with tab_auto:
    t = st.selectbox("Table to analyze", list(tables), key="auto_table")
    raw = tables[t]
    roles = current_roles(t, raw)
    typed = apply_roles(raw, roles)
    role_of = dict(zip(roles["column"], roles["role"]))
    dates = [c for c, r in role_of.items() if r == "date"]
    measures = [c for c, r in role_of.items() if r == "measure"]
    cats = [c for c, r in role_of.items() if r == "category"]
    st.caption(
        f"Using {len(dates)} date, {len(measures)} measure and {len(cats)} category "
        "column(s). Change roles in the Column roles tab."
    )

    # 1. data quality
    st.subheader("1. Data quality")
    issues = []
    dup = int(raw.duplicated().sum())
    if dup:
        issues.append(f"{dup:,} duplicate rows")
    for c in raw.columns:
        miss = raw[c].isna().mean() * 100
        if miss >= 20:
            issues.append(f"`{c}` is {miss:.0f}% missing")
        if raw[c].nunique(dropna=True) <= 1:
            issues.append(f"`{c}` has only one value")
    if issues:
        st.warning("\n".join(f"- {i}" for i in issues))
    else:
        st.success("No obvious problems: no duplicates, no mostly-empty columns.")

    # 2. trend over time
    st.subheader("2. Trend over time")
    if dates:
        d = st.selectbox("Date column", dates, key="auto_date")
        m = st.selectbox("Measure", ["(count rows)"] + measures, key="auto_measure")
        agg = st.selectbox("Aggregation", ["sum", "mean"], key="auto_agg")
        period = st.radio("Period", ["Month", "Week", "Day"], horizontal=True, key="auto_period")
        freq = {"Month": "M", "Week": "W", "Day": "D"}[period]
        ts = typed.dropna(subset=[d]).copy()
        if ts.empty:
            st.warning(f"`{d}` has no values that could be read as dates.")
        else:
            ts["period"] = ts[d].dt.to_period(freq).dt.to_timestamp()
            if m == "(count rows)":
                series = ts.groupby("period").size()
            else:
                series = ts.groupby("period")[m].agg(agg)
            series = series.rename("value").reset_index()
            st.plotly_chart(px.line(series, x="period", y="value", markers=True))
            chg = series.copy()
            chg["change_%"] = (chg["value"].pct_change() * 100).replace([np.inf, -np.inf], np.nan).round(1)
            chg = chg.dropna(subset=["change_%"])
            biggest = chg.reindex(chg["change_%"].abs().sort_values(ascending=False).index).head(5)
            st.caption(
                "Largest period-to-period changes. The first and last periods are often "
                "incomplete, so treat them with care."
            )
            st.dataframe(biggest, hide_index=True)
    else:
        st.info("No date column found. Set one in the Column roles tab to see trends.")

    # 3. category breakdown
    st.subheader("3. Breakdown by category")
    if cats:
        c = st.selectbox("Category column", cats, key="auto_cat")
        m2 = st.selectbox("Measure", ["(count rows)"] + measures, key="auto_cat_measure")
        if m2 == "(count rows)":
            g = typed.groupby(c, dropna=False).size()
        else:
            g = typed.groupby(c, dropna=False)[m2].sum()
        g = g.rename("value").reset_index().sort_values("value", ascending=False)
        g["share_%"] = (100 * g["value"] / g["value"].sum()).round(2)
        g["cumulative_%"] = g["share_%"].cumsum().round(2)
        st.caption("Totals use the sum of the measure. Use '(count rows)' when summing makes no sense (for example scores).")
        st.dataframe(g.head(15), hide_index=True)
        st.plotly_chart(px.bar(g.head(15), x=c, y="value"))
    else:
        st.info("No category column found.")

    # 4. distributions and outliers
    st.subheader("4. Distributions and outliers")
    if measures:
        rows = []
        for mm in measures:
            s = typed[mm].dropna()
            if s.empty:
                continue
            q1, q3 = s.quantile([0.25, 0.75])
            iqr = q3 - q1
            n_out = int(((s < q1 - 1.5 * iqr) | (s > q3 + 1.5 * iqr)).sum())
            rows.append({
                "measure": mm,
                "mean": round(s.mean(), 2),
                "median": round(s.median(), 2),
                "min": s.min(),
                "max": s.max(),
                "outliers": n_out,
                "outliers_%": round(100 * n_out / len(s), 2),
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True)
        st.caption("Outliers use the 1.5 x IQR rule. An outlier is unusual, not necessarily wrong.")
    else:
        st.info("No measure column found.")

    # 5. correlations
    st.subheader("5. Correlations")
    if len(measures) >= 2:
        cols = measures[:15]
        corr = typed[cols].corr(numeric_only=True)
        st.plotly_chart(
            px.imshow(corr, text_auto=".2f", zmin=-1, zmax=1, color_continuous_scale="RdBu_r")
        )
        pairs = corr.where(np.triu(np.ones(corr.shape, dtype=bool), k=1)).stack().reset_index()
        pairs.columns = ["measure_a", "measure_b", "correlation"]
        pairs = pairs.reindex(pairs["correlation"].abs().sort_values(ascending=False).index).head(5)
        st.dataframe(pairs.round(3), hide_index=True)
        st.caption("Correlation shows that two measures move together, not that one causes the other.")
    else:
        st.info("Need at least two measure columns.")

# ---------- insights ----------
with tab_insights:
    t = st.selectbox("Table", list(tables), key="ins_table")
    raw = tables[t]
    roles = current_roles(t, raw)
    typed = apply_roles(raw, roles)
    role_of = dict(zip(roles["column"], roles["role"]))
    i_dates = [c for c, r in role_of.items() if r == "date"]
    i_measures = [c for c, r in role_of.items() if r == "measure"]
    i_cats = [c for c, r in role_of.items() if r == "category"]

    c1, c2, c3, c4 = st.columns(4)
    d = c1.selectbox("Date column", ["(none)"] + i_dates, key="ins_date")
    m = c2.selectbox("Measure", ["(count rows)"] + i_measures, key="ins_measure")
    agg = c3.selectbox("Aggregation", ["sum", "mean"], key="ins_agg")
    c = c4.selectbox("Group by", ["(none)"] + i_cats, key="ins_cat")

    facts, bullets = build_facts(
        t, raw, typed, roles,
        None if d == "(none)" else d,
        None if m == "(count rows)" else m,
        None if c == "(none)" else c,
        agg,
    )

    st.subheader("Rule-based insights")
    st.caption("Calculated directly from your data with no AI involved. Always available.")
    for b in bullets:
        st.markdown(f"- {b}")
    st.caption("Sums are meaningless for some measures (for example scores), so use 'mean' there.")

    with st.expander("Facts behind these insights (this is all that is sent to the AI)"):
        st.json(facts)

    st.subheader("AI-written summary")
    st.caption(
        "Sends only the facts above, never your raw rows. Column names and group labels "
        "in the facts are included. The AI can misread or over-explain numbers, so check "
        "its text against the facts."
    )

    if get_api_key():
        st.caption(f"Using Gemini model `{MODEL}` with the API key from your .env file.")
    else:
        st.error(
            "GEMINI_API_KEY not found. Add this line to the .env file next to app.py, "
            "then restart the app:  GEMINI_API_KEY=your_key_here"
        )

    facts_hash = hashlib.md5(json.dumps(facts, sort_keys=True, default=str).encode()).hexdigest()
    state_key = f"ai::{facts_hash}"

    if st.button("Generate AI summary", disabled=not get_api_key()):
        try:
            with st.spinner("Asking Gemini..."):
                st.session_state[state_key] = generate_ai_summary(facts)
        except ImportError:
            st.error("The Gemini SDK is missing. Run: python -m pip install google-genai")
        except Exception as e:
            st.error(f"AI request failed: {e}")

    if state_key in st.session_state:
        summary = st.session_state[state_key]
        st.markdown(summary)
        flagged = numbers_to_check(summary, facts)
        if flagged:
            st.warning(
                "Numbers in the AI text that are not in the facts (check them): "
                + ", ".join(flagged)
            )
        else:
            st.success("Every number in the AI text appears in the facts.")

# ---------- relationships ----------
with tab_rel:
    st.write(
        "Looks for columns with the same name in two tables and checks how many "
        "values they share. A high overlap suggests a join key. Verify before relying on it."
    )
    if st.button("Find join candidates"):
        result = find_join_candidates(tables)
        if result.empty:
            st.warning("No shared column names found between tables.")
        else:
            st.dataframe(result, hide_index=True)

# ---------- SQL ----------
with tab_sql:
    st.write("Tables: " + ", ".join(tables))
    default_sql = f"SELECT * FROM {next(iter(tables))} LIMIT 10"
    sql = st.text_area("Write a SELECT query (DuckDB SQL)", value=default_sql, height=160)
    if st.button("Run query"):
        if not is_safe_query(sql):
            st.error("Only a single SELECT or WITH query is allowed.")
        else:
            try:
                res = con.execute(sql.strip().rstrip(";")).df()
                st.caption(f"{len(res):,} rows returned (showing up to 1,000)")
                st.dataframe(res.head(1000))
                st.download_button(
                    "Download result as CSV",
                    res.to_csv(index=False).encode("utf-8"),
                    file_name="query_result.csv",
                )
            except Exception as e:
                st.error(f"Query failed: {e}")

# ---------- charts ----------
with tab_chart:
    t = st.selectbox("Table", list(tables), key="chart_table")
    df = tables[t]
    num_cols = df.select_dtypes("number").columns.tolist()
    kind = st.radio("Chart type", ["Bar", "Line", "Histogram", "Scatter"], horizontal=True)

    if kind == "Histogram":
        if not num_cols:
            st.warning("This table has no numeric columns.")
        else:
            x = st.selectbox("Column", num_cols, key="hist_x")
            st.plotly_chart(px.histogram(df, x=x, nbins=50))
    elif kind == "Scatter":
        if len(num_cols) < 2:
            st.warning("Need at least two numeric columns.")
        else:
            x = st.selectbox("X", num_cols, key="sc_x")
            y = st.selectbox("Y", num_cols, index=1, key="sc_y")
            sample = df.sample(min(len(df), 5000), random_state=0)
            st.plotly_chart(px.scatter(sample, x=x, y=y))
    else:
        x = st.selectbox("Group by (X)", df.columns.tolist(), key="grp_x")
        y = st.selectbox("Value (Y)", ["(count rows)"] + num_cols, key="grp_y")
        agg = st.selectbox("Aggregation", ["sum", "mean", "count"], key="grp_agg")
        top_n = st.slider("Max groups (bar chart)", 5, 50, 15)

        if y == "(count rows)":
            grouped = df.groupby(x, dropna=False).size().reset_index(name="value")
        else:
            grouped = df.groupby(x, dropna=False)[y].agg(agg).reset_index(name="value")

        if kind == "Bar":
            grouped = grouped.sort_values("value", ascending=False).head(top_n)
            st.plotly_chart(px.bar(grouped, x=x, y="value"))
        else:
            grouped = grouped.sort_values(x)
            st.plotly_chart(px.line(grouped, x=x, y="value"))