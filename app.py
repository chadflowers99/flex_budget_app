from __future__ import annotations

import ast
import calendar
import operator
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st


APP_DIR = Path(__file__).parent
BILLS_PATH = APP_DIR / "bills.csv"
CATALOG_PATH = APP_DIR / "bill_catalog.csv"
CASHFLOW_PATH = APP_DIR / "cash_flow.csv"
WEEK_PERIODS = ["wk1", "wk2", "wk3", "wk4", "wk5"]

DEFAULT_BILLS = pd.DataFrame(
    [
        ["wk1", "rent", 700.0],
        ["wk1", "fuel", 60.0],
        ["wk1", "tithe", 0.0],
        ["wk1", "groc", 50.0],
        ["wk2", "ba", 0.0],
        ["wk2", "ba2", 0.0],
        ["wk2", "fuel", 0.0],
        ["wk2", "tithe", 0.0],
        ["wk2", "groc", 0.0],
        ["wk3", "cc", 0.0],
        ["wk3", "av", 0.0],
        ["wk3", "rp", 0.0],
        ["wk3", "dc", 0.0],
        ["wk3", "s", 0.0],
        ["wk3", "dp", 0.0],
        ["wk3", "chse", 0.0],
        ["wk3", "pp", 0.0],
        ["wk3", "tithe", 0.0],
        ["wk3", "fuel", 0.0],
        ["wk3", "groc", 0.0],
        ["wk4", "car", 0.0],
        ["wk4", "fuel", 0.0],
        ["wk4", "tithe", 0.0],
        ["wk4", "groc", 0.0],
        ["wk5", "rent", 0.0],
        ["wk5", "ins", 0.0],
        ["wk5", "ufcu", 0.0],
        ["wk5", "fuel", 0.0],
        ["wk5", "groc", 0.0],
        ["wk5", "phn", 0.0],
        ["wk5", "elec", 0.0],
        ["wk5", "ngas", 0.0],
        ["wk5", "tithe", 0.0],
    ],
    columns=["period", "bill", "amount"],
)

def normalize_period(value: str) -> str:
    return str(value).strip().lower()


def load_table(path: Path, default_df: pd.DataFrame) -> pd.DataFrame:
    if path.exists():
        df = pd.read_csv(path)
    else:
        df = default_df.copy()
    return df


def load_bill_catalog(default_values: list[str], bills_df: pd.DataFrame) -> list[str]:
    if CATALOG_PATH.exists():
        try:
            catalog_df = pd.read_csv(CATALOG_PATH)
            file_values = catalog_df["bill"].astype(str).tolist() if "bill" in catalog_df.columns else []
        except Exception:
            file_values = []
    else:
        file_values = []

    bill_values = bills_df["bill"].astype(str).tolist() if not bills_df.empty else []
    return ensure_bill_catalog(file_values + default_values + bill_values)


def load_cash_flow() -> dict[str, float]:
    defaults = {period: 0.0 for period in WEEK_PERIODS}
    if not CASHFLOW_PATH.exists():
        return defaults

    try:
        cashflow_df = pd.read_csv(CASHFLOW_PATH)
    except Exception:
        return defaults

    required_cols = {"period", "cash_flow"}
    if not required_cols.issubset(set(cashflow_df.columns)):
        return defaults

    out = defaults.copy()
    for row in cashflow_df.itertuples(index=False):
        period = normalize_period(str(row.period))
        if period in out:
            out[period] = float(pd.to_numeric(row.cash_flow, errors="coerce") if row.cash_flow is not None else 0.0)
            if pd.isna(out[period]):
                out[period] = 0.0
    return out


def parse_numeric_text(value: str | float | int | None, allow_expression: bool = False) -> float:
    if value is None:
        return 0.0
    cleaned = str(value).replace("$", "").replace(",", "").strip()
    if cleaned == "":
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        if allow_expression:
            try:
                return safe_eval_expression(cleaned)
            except Exception:
                return 0.0
        return 0.0


def build_weekly_snapshot_csv(period: str, bills_df: pd.DataFrame, cash_flow: float, net_cash_flow: float) -> str:
    snapshot_rows = bills_df.copy()
    if snapshot_rows.empty:
        snapshot_rows = pd.DataFrame(columns=["bill", "amount"])

    totals = pd.DataFrame(
        [
            {"bill": "TOTAL_BILLS", "amount": float(snapshot_rows["amount"].sum()) if not snapshot_rows.empty else 0.0},
            {"bill": "CASH_FLOW", "amount": cash_flow},
            {"bill": "NET_CASH_FLOW", "amount": net_cash_flow},
        ]
    )
    out = pd.concat([snapshot_rows, totals], ignore_index=True)
    out.insert(0, "period", period)
    return out.to_csv(index=False)


def persist_state(bills_df: pd.DataFrame, bill_catalog: list[str], cash_flow_by_period: dict[str, float]) -> None:
    bills_df.to_csv(BILLS_PATH, index=False)
    pd.DataFrame({"bill": ensure_bill_catalog(bill_catalog)}).to_csv(CATALOG_PATH, index=False)
    cash_rows = [{"period": p, "cash_flow": float(cash_flow_by_period.get(p, 0.0))} for p in WEEK_PERIODS]
    pd.DataFrame(cash_rows).to_csv(CASHFLOW_PATH, index=False)


def clean_bills(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["period", "bill", "amount"])

    out = df.copy()
    # Backward compatibility for older files that use `price`.
    if "amount" not in out.columns and "price" in out.columns:
        out["amount"] = out["price"]

    for col in ["period", "bill", "amount"]:
        if col not in out.columns:
            out[col] = "" if col != "amount" else 0

    out = out[["period", "bill", "amount"]]
    out["period"] = out["period"].astype(str).map(normalize_period)
    out["bill"] = out["bill"].astype(str).str.strip()
    out["amount"] = (
        out["amount"]
        .astype(str)
        .str.replace("$", "", regex=False)
        .str.replace(",", "", regex=False)
        .str.strip()
        .pipe(pd.to_numeric, errors="coerce")
        .fillna(0.0)
    )
    out = out[(out["period"] != "") & (out["bill"] != "")]
    return out.reset_index(drop=True)


def period_order_from_data(bills_df: pd.DataFrame) -> list[str]:
    seen: list[str] = []

    def add_periods(values: pd.Series) -> None:
        for p in values.astype(str).tolist():
            p = normalize_period(p)
            if p and p not in seen:
                seen.append(p)

    if not bills_df.empty:
        add_periods(bills_df["period"])

    return seen


def clean_period_bills(df: pd.DataFrame, period: str) -> pd.DataFrame:
    out = df.copy()
    for col in ["bill", "amount"]:
        if col not in out.columns:
            out[col] = "" if col == "bill" else 0

    out = out[["bill", "amount"]]
    out["bill"] = out["bill"].astype(str).str.strip()
    out["amount"] = (
        out["amount"]
        .astype(str)
        .str.replace("$", "", regex=False)
        .str.replace(",", "", regex=False)
        .str.strip()
        .pipe(pd.to_numeric, errors="coerce")
        .fillna(0.0)
    )
    out = out[out["bill"] != ""].reset_index(drop=True)
    out.insert(0, "period", period)
    return out


def ensure_periods_order(periods: list[str]) -> list[str]:
    cleaned: list[str] = []
    for p in periods:
        np = normalize_period(p)
        if np and np not in cleaned:
            cleaned.append(np)
    return cleaned


def ensure_bill_catalog(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    for v in values:
        nv = str(v).strip()
        if nv and nv not in cleaned:
            cleaned.append(nv)
    return cleaned


def migrate_periods_to_weeks(bills_df: pd.DataFrame) -> pd.DataFrame:
    if bills_df.empty:
        return bills_df

    current_periods = ensure_periods_order(bills_df["period"].astype(str).tolist())
    if all(p in WEEK_PERIODS for p in current_periods):
        return bills_df

    mapping: dict[str, str] = {}
    for idx, period in enumerate(current_periods):
        mapping[period] = WEEK_PERIODS[idx] if idx < len(WEEK_PERIODS) else WEEK_PERIODS[-1]

    out = bills_df.copy()
    out["period"] = out["period"].astype(str).map(lambda p: mapping.get(normalize_period(p), WEEK_PERIODS[-1]))
    return out


def build_period_amount_cache(bills_df: pd.DataFrame) -> dict[str, dict[str, float]]:
    cache: dict[str, dict[str, float]] = {p: {} for p in WEEK_PERIODS}
    if bills_df.empty:
        return cache

    grouped = bills_df.groupby(["period", "bill"], as_index=False)["amount"].sum()
    for row in grouped.itertuples(index=False):
        period = normalize_period(str(row.period))
        bill = str(row.bill).strip()
        if period in cache and bill:
            cache[period][bill] = float(row.amount)
    return cache


def shift_calendar_month(delta: int) -> None:
    month = int(st.session_state.calendar_month)
    year = int(st.session_state.calendar_year)

    month += delta
    if month < 1:
        month = 12
        year -= 1
    elif month > 12:
        month = 1
        year += 1

    st.session_state.calendar_month = month
    st.session_state.calendar_year = year


def safe_eval_expression(expr: str) -> float:
    # Allow only basic arithmetic for calculator input.
    allowed_bin_ops = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Pow: operator.pow,
        ast.Mod: operator.mod,
    }
    allowed_unary_ops = {
        ast.UAdd: operator.pos,
        ast.USub: operator.neg,
    }

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in allowed_bin_ops:
            return allowed_bin_ops[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in allowed_unary_ops:
            return allowed_unary_ops[type(node.op)](_eval(node.operand))
        raise ValueError("Only numbers and + - * / % ** operators are allowed.")

    parsed = ast.parse(expr, mode="eval")
    return float(_eval(parsed))


def main() -> None:
    st.set_page_config(page_title="Flexible Budget", layout="wide")

    header_col, calendar_col = st.columns([3, 2])
    with header_col:
        st.markdown(
            """
            <div>
                <h1 style='margin: 0; line-height: 1.05;'>Portfolio brand.</h1>
                <div style='font-size: 0.72rem;'>
                    FLEXIBLE BUDGET PLANNER
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with calendar_col:
        st.subheader("Calendar")
        if "calendar_year" not in st.session_state or "calendar_month" not in st.session_state:
            today = date.today()
            st.session_state.calendar_year = today.year
            st.session_state.calendar_month = today.month

        nav_col1, nav_col2, nav_col3 = st.columns([1, 3, 1])
        with nav_col1:
            st.button("<", key="calendar_prev_month", on_click=shift_calendar_month, args=(-1,))
        with nav_col2:
            st.caption(f"{calendar.month_name[st.session_state.calendar_month]} {st.session_state.calendar_year}")
        with nav_col3:
            st.button(">", key="calendar_next_month", on_click=shift_calendar_month, args=(1,))

        sunday_first_cal = calendar.Calendar(firstweekday=6)
        month_grid = sunday_first_cal.monthdayscalendar(st.session_state.calendar_year, st.session_state.calendar_month)
        month_df = pd.DataFrame(month_grid, columns=["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"])
        month_df = month_df.replace(0, "")
        month_df = month_df.astype(str)
        header_html = "".join(
            [
                f"<th style='text-align:left;padding:4px 6px;border-bottom:1px solid #d0d7de;font-size:0.8rem;'>{col}</th>"
                for col in month_df.columns
            ]
        )
        row_html = ""
        for row in month_df.itertuples(index=False):
            cells = "".join(
                [
                    f"<td style='text-align:left;padding:4px 6px;font-size:0.8rem;vertical-align:top;'>{cell}</td>"
                    for cell in row
                ]
            )
            row_html += f"<tr>{cells}</tr>"

        st.markdown(
            f"""
            <table style='width:100%;border-collapse:collapse;table-layout:fixed;'>
                <thead><tr>{header_html}</tr></thead>
                <tbody>{row_html}</tbody>
            </table>
            """,
            unsafe_allow_html=True,
        )

    if "bills" not in st.session_state:
        st.session_state.bills = migrate_periods_to_weeks(clean_bills(load_table(BILLS_PATH, DEFAULT_BILLS)))
    if "periods_order" not in st.session_state:
        st.session_state.periods_order = WEEK_PERIODS.copy()
    if "bill_catalog" not in st.session_state:
        base_catalog = DEFAULT_BILLS["bill"].astype(str).tolist()
        st.session_state.bill_catalog = load_bill_catalog(base_catalog, st.session_state.bills)
    if "period_amount_cache" not in st.session_state:
        st.session_state.period_amount_cache = build_period_amount_cache(st.session_state.bills)
    if "cash_flow_by_period" not in st.session_state:
        st.session_state.cash_flow_by_period = load_cash_flow()

    periods = WEEK_PERIODS.copy()

    st.subheader("Bills by Period")
    st.caption("Select bills from the dropdown for each week, then edit amounts.")

    catalog_col1, catalog_col2 = st.columns([3, 1])
    with catalog_col1:
        new_bill_name = st.text_input("Add bill to dropdown", placeholder="internet")
    with catalog_col2:
        if st.button("Add bill option"):
            candidate = str(new_bill_name).strip()
            if not candidate:
                st.warning("Enter a bill name.")
            elif candidate in st.session_state.bill_catalog:
                st.info("Bill already exists in dropdown.")
            else:
                st.session_state.bill_catalog = ensure_bill_catalog(st.session_state.bill_catalog + [candidate])
                st.success(f"Added bill option: {candidate}")
                persist_state(st.session_state.bills, st.session_state.bill_catalog, st.session_state.cash_flow_by_period)
                st.rerun()
    bill_catalog = ensure_bill_catalog(st.session_state.bill_catalog)
    if not bill_catalog:
        bill_catalog = ensure_bill_catalog(DEFAULT_BILLS["bill"].astype(str).tolist())
        st.session_state.bill_catalog = bill_catalog

    bill_tabs = st.tabs(periods)
    rebuilt_bills: list[pd.DataFrame] = []

    for i, period in enumerate(periods):
        with bill_tabs[i]:
            period_rows = st.session_state.bills[st.session_state.bills["period"] == period][["bill", "amount"]].copy()
            grouped = period_rows.groupby("bill", as_index=False)["amount"].sum() if not period_rows.empty else pd.DataFrame(columns=["bill", "amount"])
            existing_bill_set = set(grouped["bill"].astype(str).tolist()) if not grouped.empty else set()
            # Keep a stable order across refreshes by following persistent catalog order.
            existing_bills = [bill for bill in bill_catalog if bill in existing_bill_set]

            if period not in st.session_state.period_amount_cache:
                st.session_state.period_amount_cache[period] = {}
            cache_for_period = st.session_state.period_amount_cache[period]
            if not grouped.empty:
                for row in grouped.itertuples(index=False):
                    bill_name = str(row.bill)
                    if bill_name not in cache_for_period:
                        cache_for_period[bill_name] = float(row.amount)

            selected_bills = st.multiselect(
                f"Bills for {period}",
                options=bill_catalog,
                default=existing_bills,
                key=f"bill_picker_{period}",
            )

            pending_zero_key = f"pending_zero_{period}"
            if st.session_state.get(pending_zero_key, False):
                st.session_state.period_amount_cache[period] = {bill_name: 0.0 for bill_name in selected_bills}
                for bill_name in selected_bills:
                    amount_key = f"amount_input_{period}_{bill_name}"
                    st.session_state[amount_key] = "0.00"
                cash_flow_key_reset = f"cash_flow_{period}"
                st.session_state[cash_flow_key_reset] = "0.00"
                st.session_state.cash_flow_by_period[period] = 0.0
                st.session_state[pending_zero_key] = False
                st.success(f"Amounts and cash flow cleared for {period}.")

            # Keep cache aligned with currently selected bills for this period.
            st.session_state.period_amount_cache[period] = {
                bill_name: float(cache_for_period.get(bill_name, 0.0)) for bill_name in selected_bills
            }

            period_entries: list[dict[str, float | str]] = []
            if selected_bills:
                col_count = min(7, max(1, len(selected_bills)))
                input_cols = st.columns(col_count)
                for idx, bill_name in enumerate(selected_bills):
                    amount_key = f"amount_input_{period}_{bill_name}"
                    default_amount = float(st.session_state.period_amount_cache[period].get(bill_name, 0.0))
                    if amount_key not in st.session_state:
                        st.session_state[amount_key] = f"{default_amount:.2f}"

                    with input_cols[idx % col_count]:
                        st.markdown(
                            f"<div style='font-size:0.72rem; line-height:1; margin-bottom:0.1rem;'>{bill_name}</div>",
                            unsafe_allow_html=True,
                        )
                        amount_value = st.text_input(
                            f"{bill_name} amount",
                            key=amount_key,
                            label_visibility="collapsed",
                            placeholder="0.00",
                        )
                    period_entries.append({"bill": bill_name, "amount": parse_numeric_text(amount_value)})

            edited_period = pd.DataFrame(period_entries, columns=["bill", "amount"])
            cleaned_period = clean_period_bills(edited_period, period)
            st.session_state.period_amount_cache[period] = {
                str(row.bill): float(row.amount) for row in cleaned_period.itertuples(index=False)
            }

            period_total = float(pd.to_numeric(cleaned_period["amount"], errors="coerce").fillna(0.0).sum())
            cash_flow_key = f"cash_flow_{period}"
            if cash_flow_key not in st.session_state:
                st.session_state[cash_flow_key] = f"{float(st.session_state.cash_flow_by_period.get(period, 0.0)):.2f}"
            period_cash_flow = st.text_input(
                "Enter cash flow",
                key=cash_flow_key,
                placeholder="0.00",
            )
            period_cash_flow_value = parse_numeric_text(period_cash_flow, allow_expression=True)
            st.session_state.cash_flow_by_period[period] = period_cash_flow_value
            period_remaining = period_cash_flow_value - period_total
            total_col, net_col = st.columns(2)
            total_col.metric(f"Total bills for {period}", f"${period_total:,.2f}")
            net_col.metric(f"Net cash flow for {period}", f"${period_remaining:,.2f}")

            cue_color = "#15803d" if period_remaining >= 0 else "#b91c1c"
            cue_text = "positive" if period_remaining >= 0 else "negative"
            st.markdown(
                f"<div style='font-size:0.8rem;color:{cue_color};font-weight:600;'>Net cue: {cue_text}</div>",
                unsafe_allow_html=True,
            )

            action_col1, action_col2 = st.columns(2)
            with action_col1:
                if st.button("Refresh amounts", key=f"zero_all_{period}"):
                    st.session_state[pending_zero_key] = True
                    st.rerun()

            with action_col2:
                snapshot_csv = build_weekly_snapshot_csv(period, cleaned_period[["bill", "amount"]], period_cash_flow_value, period_remaining)
                st.download_button(
                    label="Snapshot CSV",
                    data=snapshot_csv.encode("utf-8"),
                    file_name=f"{period}_snapshot.csv",
                    mime="text/csv",
                    key=f"download_snapshot_{period}",
                    on_click="ignore",
                )

            rebuilt_bills.append(cleaned_period)

    st.session_state.bills = clean_bills(
        pd.concat(rebuilt_bills, ignore_index=True) if rebuilt_bills else pd.DataFrame(columns=["period", "bill", "amount"])
    )
    st.session_state.bill_catalog = ensure_bill_catalog(
        bill_catalog + st.session_state.bills["bill"].astype(str).tolist()
    )
    persist_state(st.session_state.bills, st.session_state.bill_catalog, st.session_state.cash_flow_by_period)

    st.session_state.periods_order = WEEK_PERIODS.copy()

if __name__ == "__main__":
    main()
