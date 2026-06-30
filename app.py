from __future__ import annotations

import streamlit as st

# MUST BE FIRST STREAMLIT COMMAND
st.set_page_config(page_title="Flexible Budget", layout="wide")

import ast
import calendar
import json
import operator
import tempfile
from datetime import date
from pathlib import Path

import pandas as pd
from supabase import create_client, Client
from supabase.client import ClientOptions

# Load Supabase credentials from secrets.
supabase_block = st.secrets.get("supabase", {})

SUPABASE_URL = (
    st.secrets.get("SUPABASE_URL")
    or supabase_block.get("SUPABASE_URL")
    or supabase_block.get("url")
)
SUPABASE_ANON_KEY = (
    st.secrets.get("SUPABASE_ANON_KEY")
    or st.secrets.get("SUPABASE_KEY")
    or supabase_block.get("SUPABASE_ANON_KEY")
    or supabase_block.get("SUPABASE_KEY")
    or supabase_block.get("anon_key")
)

if isinstance(SUPABASE_URL, str):
    SUPABASE_URL = SUPABASE_URL.strip()
if isinstance(SUPABASE_ANON_KEY, str):
    SUPABASE_ANON_KEY = SUPABASE_ANON_KEY.strip()

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    st.error(
        "Missing SUPABASE_URL and/or Supabase key in secrets. "
        "Expected SUPABASE_ANON_KEY (preferred) or SUPABASE_KEY (legacy)."
    )
    st.stop()

if "sb_secret_" in SUPABASE_ANON_KEY.lower() or "service_role" in SUPABASE_ANON_KEY.lower():
    st.error(
        "SUPABASE_ANON_KEY appears to be a service-role/secret key. "
        "Use the anon/publishable key from Supabase Settings > API."
    )
    st.stop()

if "your-project" in SUPABASE_URL or "your-project-ref" in SUPABASE_URL:
    st.error(
        "SUPABASE_URL in .streamlit/secrets.toml is still a placeholder. "
        "Use your real project URL from Supabase Settings > API."
    )
    st.stop()

if "your_anon_key_here" in SUPABASE_ANON_KEY.lower():
    st.error(
        "SUPABASE_ANON_KEY in .streamlit/secrets.toml is still a placeholder. "
        "Use your real anon/publishable key from Supabase Settings > API."
    )
    st.stop()


BASE_DIR = Path(__file__).resolve().parent
AUTH_STORAGE_FILE = BASE_DIR / ".streamlit" / "supabase_auth_storage.json"
AUTH_STORAGE_FILE_FALLBACK = Path(tempfile.gettempdir()) / "flex_budget_app_supabase_auth_storage.json"


class MemoryAuthStorage:
    """In-memory auth storage fallback when filesystem is not writable."""
    def __init__(self):
        self._store = {}

    def get_item(self, key):
        return self._store.get(key)

    def set_item(self, key, value):
        self._store[key] = value

    def remove_item(self, key):
        self._store.pop(key, None)


class FileAuthStorage:
    """File-backed auth storage — required for PKCE verifier across redirects."""
    def __init__(self, storage_file):
        self.storage_file = storage_file

    def _read(self):
        if not self.storage_file.exists():
            return {}
        try:
            return json.loads(self.storage_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _write(self, data):
        self.storage_file.parent.mkdir(parents=True, exist_ok=True)
        self.storage_file.write_text(json.dumps(data), encoding="utf-8")

    def get_item(self, key):
        return self._read().get(key)

    def set_item(self, key, value):
        data = self._read()
        data[key] = value
        self._write(data)

    def remove_item(self, key):
        data = self._read()
        data.pop(key, None)
        self._write(data)


def _is_writable(path: Path) -> bool:
    """Checks whether a path is writable by creating/removing a tiny probe file."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        probe = path.parent / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _build_auth_storage():
    """Use file-backed storage when possible to preserve PKCE verifier on callback."""
    if _is_writable(AUTH_STORAGE_FILE):
        return FileAuthStorage(AUTH_STORAGE_FILE)
    if _is_writable(AUTH_STORAGE_FILE_FALLBACK):
        return FileAuthStorage(AUTH_STORAGE_FILE_FALLBACK)
    return MemoryAuthStorage()


# Initialize Supabase client with appropriate storage backend.
@st.cache_resource
def get_supabase_client() -> Client:
    storage = _build_auth_storage()
    return create_client(
        SUPABASE_URL,
        SUPABASE_ANON_KEY,
        options=ClientOptions(
            flow_type="pkce",
            storage=storage,
        ),
    )


supabase: Client = get_supabase_client()

# Legacy paths (no longer used, but kept for reference)
APP_DIR = Path(__file__).parent
BILLS_PATH = APP_DIR / "bills.csv"
CATALOG_PATH = APP_DIR / "bill_catalog.csv"
CASHFLOW_PATH = APP_DIR / "cash_flow.csv"


def auth_ui():
    """Displays login/signup UI and manages authentication state."""
    if "user" not in st.session_state:
        st.session_state.user = None

    if st.session_state.user:
        return st.session_state.user

    # Restore session from file-backed storage after page refresh
    if not st.session_state.user:
        try:
            existing = supabase.auth.get_session()
            if existing and existing.user:
                st.session_state.user = existing.user
                st.session_state.access_token = existing.access_token
                return st.session_state.user
        except Exception:
            pass

    oauth_error = st.query_params.get("error")
    oauth_error_description = st.query_params.get("error_description")
    if oauth_error:
        st.error(
            f"Login attempt failed: {oauth_error}"
            + (f" ({oauth_error_description})" if oauth_error_description else "")
        )
        st.query_params.clear()

    # Handle OAuth callback from Supabase (PKCE flow).
    auth_code = st.query_params.get("code")
    if auth_code:
        try:
            response = supabase.auth.exchange_code_for_session({"auth_code": auth_code})
            if response and response.user:
                st.session_state.user = response.user
                st.session_state.access_token = response.session.access_token if response.session else None
                st.query_params.clear()
                st.rerun()
            else:
                st.error("Login attempt failed: no user returned from Supabase callback.")
                st.query_params.clear()
        except Exception as e:
            st.error(f"Login attempt failed: {str(e)}")
            st.query_params.clear()

    st.markdown("### Authentication")
    auth_tab1, auth_tab2 = st.tabs(["Login", "Sign Up"])

    with auth_tab1:
        email = st.text_input("Email", key="login_email")
        password = st.text_input("Password", type="password", key="login_password")

        if st.button("Log In", key="login_button"):
            try:
                response = supabase.auth.sign_in_with_password(
                    {
                        "email": email,
                        "password": password,
                    }
                )
                st.session_state.user = response.user
                st.session_state.access_token = response.session.access_token
                st.rerun()
            except Exception as e:
                st.error(f"Login attempt failed: {str(e)}")

    with auth_tab2:
        email = st.text_input("Email", key="signup_email")
        password = st.text_input("Password", type="password", key="signup_password")

        if st.button("Sign Up", key="signup_button"):
            try:
                response = supabase.auth.sign_up(
                    {
                        "email": email,
                        "password": password,
                    }
                )
                st.session_state.user = response.user
                if response.session:
                    st.session_state.access_token = response.session.access_token
                st.success("Account created! Log in with your credentials.")
            except Exception as e:
                st.error(f"Sign up failed: {str(e)}")

    return None
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


# Supabase load functions (replaces CSV loading)
def load_bills_from_supabase(user_id: str) -> pd.DataFrame:
    """Load bills from Supabase for the authenticated user."""
    try:
        response = supabase.table("bills").select("period, bill, amount").eq("user_id", user_id).execute()
        if response.data:
            return pd.DataFrame(response.data)[["period", "bill", "amount"]]
        return pd.DataFrame(columns=["period", "bill", "amount"])
    except Exception as e:
        st.warning(f"Failed to load bills: {str(e)}")
        return pd.DataFrame(columns=["period", "bill", "amount"])


def load_bill_catalog_from_supabase(user_id: str) -> list[str]:
    """Load available bills from Supabase for the authenticated user."""
    try:
        response = supabase.table("bill_catalog").select("bill").eq("user_id", user_id).execute()
        if response.data:
            return [row["bill"] for row in response.data if row["bill"]]
        return []
    except Exception as e:
        st.warning(f"Failed to load bill catalog: {str(e)}")
        return []


def load_cash_flow_from_supabase(user_id: str) -> dict[str, float]:
    """Load cash flow by period from Supabase for the authenticated user."""
    defaults = {period: 0.0 for period in WEEK_PERIODS}
    try:
        response = supabase.table("cash_flow").select("period, cash_flow").eq("user_id", user_id).execute()
        out = defaults.copy()
        if response.data:
            for row in response.data:
                period = normalize_period(str(row["period"]))
                if period in out:
                    out[period] = float(row["cash_flow"] if row["cash_flow"] is not None else 0.0)
        return out
    except Exception as e:
        st.warning(f"Failed to load cash flow: {str(e)}")
        return defaults


def persist_to_supabase(user_id: str, bills_df: pd.DataFrame, bill_catalog: list[str], cash_flow_by_period: dict[str, float]) -> None:
    """Save all data to Supabase for the authenticated user."""
    try:
        # Upsert bills
        if not bills_df.empty:
            bills_list = bills_df.to_dict(orient="records")
            for bill in bills_list:
                bill["user_id"] = user_id
            supabase.table("bills").upsert(bills_list, on_conflict="user_id,period,bill").execute()
        
        # Upsert bill catalog
        if bill_catalog:
            catalog_list = [{"user_id": user_id, "bill": bill} for bill in ensure_bill_catalog(bill_catalog)]
            supabase.table("bill_catalog").upsert(catalog_list, on_conflict="user_id,bill").execute()
        
        # Upsert cash flow
        cashflow_list = [
            {"user_id": user_id, "period": period, "cash_flow": float(cash_flow_by_period.get(period, 0.0))}
            for period in WEEK_PERIODS
        ]
        supabase.table("cash_flow").upsert(cashflow_list, on_conflict="user_id,period").execute()
    except Exception as e:
        st.error(f"Failed to save data: {str(e)}")


def load_table(path: Path, default_df: pd.DataFrame) -> pd.DataFrame:
    """Legacy function for backward compatibility."""
    if path.exists():
        df = pd.read_csv(path)
    else:
        df = default_df.copy()
    return df


def load_bill_catalog(default_values: list[str], bills_df: pd.DataFrame) -> list[str]:
    """Legacy function for backward compatibility."""
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
    """Legacy function for backward compatibility."""
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
    # Authenticate user first
    user = auth_ui()
    if not user:
        st.stop()
    
    user_id = user.id
    
    # Add logout button in sidebar
    with st.sidebar:
        if st.button("Logout"):
            supabase.auth.sign_out()
            st.session_state.user = None
            st.session_state.access_token = None
            st.rerun()
        st.caption(f"Logged in as: {user.email}")

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

    # Load data from Supabase or initialize session state
    if "bills" not in st.session_state:
        st.session_state.bills = migrate_periods_to_weeks(clean_bills(load_bills_from_supabase(user_id)))
    if "periods_order" not in st.session_state:
        st.session_state.periods_order = WEEK_PERIODS.copy()
    if "bill_catalog" not in st.session_state:
        catalog_from_db = load_bill_catalog_from_supabase(user_id)
        base_catalog = DEFAULT_BILLS["bill"].astype(str).tolist()
        st.session_state.bill_catalog = ensure_bill_catalog(catalog_from_db + base_catalog)
    if "period_amount_cache" not in st.session_state:
        st.session_state.period_amount_cache = build_period_amount_cache(st.session_state.bills)
    if "cash_flow_by_period" not in st.session_state:
        st.session_state.cash_flow_by_period = load_cash_flow_from_supabase(user_id)

    periods = WEEK_PERIODS.copy()

    st.subheader("Bills by Period")
    st.caption("Select bills from the dropdown for each week, then edit amounts.")

    # Bill management tabs
    bill_tab1, bill_tab2 = st.tabs(["Add bill", "Delete bill"])
    
    with bill_tab1:
        add_col1, add_col2 = st.columns([3, 1])
        with add_col1:
            new_bill_name = st.text_input("Bill name", placeholder="internet", label_visibility="collapsed")
        with add_col2:
            if st.button("Add", key="add_bill_btn"):
                candidate = str(new_bill_name).strip()
                if not candidate:
                    st.warning("Enter a bill name.")
                elif candidate in st.session_state.bill_catalog:
                    st.info("Already exists.")
                else:
                    st.session_state.bill_catalog = ensure_bill_catalog(st.session_state.bill_catalog + [candidate])
                    st.success(f"Added: {candidate}")
                    persist_to_supabase(user_id, st.session_state.bills, st.session_state.bill_catalog, st.session_state.cash_flow_by_period)
                    st.rerun()
    
    with bill_tab2:
        delete_col1, delete_col2 = st.columns([3, 1])
        with delete_col1:
            bill_to_delete = st.selectbox("Select bill", options=ensure_bill_catalog(st.session_state.bill_catalog), placeholder="Choose bill", label_visibility="collapsed")
        with delete_col2:
            if st.button("Delete", key="delete_bill_btn"):
                if bill_to_delete:
                    st.session_state.bill_catalog = [bill for bill in st.session_state.bill_catalog if bill != bill_to_delete]
                    st.success(f"Deleted: {bill_to_delete}")
                    persist_to_supabase(user_id, st.session_state.bills, st.session_state.bill_catalog, st.session_state.cash_flow_by_period)
                    st.rerun()
                else:
                    st.warning("Select a bill.")
    
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
    persist_to_supabase(user_id, st.session_state.bills, st.session_state.bill_catalog, st.session_state.cash_flow_by_period)

    st.session_state.periods_order = WEEK_PERIODS.copy()

if __name__ == "__main__":
    main()
