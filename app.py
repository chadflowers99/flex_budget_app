from __future__ import annotations

import streamlit as st

# MUST BE FIRST STREAMLIT COMMAND
st.set_page_config(page_title="Portfolio brand.", layout="wide")

import ast
import calendar
import json
import operator
import tempfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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


class SessionStateAuthStorage:
    """Session-scoped auth storage to avoid cross-user leakage on shared hosts."""
    def get_item(self, key):
        store = st.session_state.get("_supabase_auth_store", {})
        return store.get(key)

    def set_item(self, key, value):
        if "_supabase_auth_store" not in st.session_state:
            st.session_state._supabase_auth_store = {}
        st.session_state._supabase_auth_store[key] = value

    def remove_item(self, key):
        store = st.session_state.get("_supabase_auth_store", {})
        store.pop(key, None)


class FileAuthStorage:
    """File-backed auth storage with session_state fallback for Streamlit Cloud."""
    def __init__(self, storage_file):
        self.storage_file = storage_file

    def _read(self):
        # Try session_state first (fastest, survives reruns)
        if "_supabase_auth_store" in st.session_state:
            return st.session_state._supabase_auth_store
        # Fall back to file
        if not self.storage_file.exists():
            return {}
        try:
            data = json.loads(self.storage_file.read_text(encoding="utf-8"))
            st.session_state._supabase_auth_store = data
            return data
        except (OSError, json.JSONDecodeError):
            return {}

    def _write(self, data):
        # Always write to session_state (fastest)
        st.session_state._supabase_auth_store = data
        # Also write to file if possible
        try:
            self.storage_file.parent.mkdir(parents=True, exist_ok=True)
            self.storage_file.write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            pass  # File write failed, but session_state has it

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
    """Use session-scoped storage to avoid cross-user leakage on shared hosts."""
    return SessionStateAuthStorage()


def _resolve_oauth_redirect_url() -> str:
    """Build OAuth callback URL for current host, with secrets override support."""
    configured = (
        st.secrets.get("OAUTH_REDIRECT_URL")
        or st.secrets.get("APP_REDIRECT_URL")
        or (supabase_block.get("OAUTH_REDIRECT_URL") if isinstance(supabase_block, dict) else None)
    )
    if isinstance(configured, str) and configured.strip():
        return configured.strip()

    try:
        headers = getattr(st.context, "headers", {})
        host = (headers.get("x-forwarded-host") or headers.get("host") or "").strip()
        proto = (headers.get("x-forwarded-proto") or "https").split(",")[0].strip()
        if host:
            if host.startswith("localhost") or host.startswith("127.0.0.1"):
                proto = "http"
            return f"{proto}://{host}"
    except Exception:
        pass

    return "https://pb-flexbudget.streamlit.app"


def app_today() -> date:
    """Return today's date using configured/app-local timezone.

    Optional override: set APP_TIMEZONE in Streamlit secrets (e.g. America/Chicago).
    """
    # Prefer explicit app setting.
    timezone_name = st.secrets.get("APP_TIMEZONE")
    if isinstance(timezone_name, str) and timezone_name.strip():
        try:
            return datetime.now(ZoneInfo(timezone_name.strip())).date()
        except Exception:
            pass

    # Next, try common proxy/browser timezone headers when available.
    try:
        headers = getattr(st.context, "headers", {})
        timezone_header_keys = [
            "x-time-zone",
            "x-timezone",
            "cf-timezone",
            "cloudfront-viewer-time-zone",
        ]
        for key in timezone_header_keys:
            header_tz = str(headers.get(key) or "").strip()
            if not header_tz:
                continue
            try:
                return datetime.now(ZoneInfo(header_tz)).date()
            except Exception:
                continue
    except Exception:
        pass

    # Fallback: choose a US-local timezone to avoid UTC rollover showing next day.
    try:
        return datetime.now(ZoneInfo("America/Chicago")).date()
    except Exception:
        return datetime.now().astimezone().date()


# Initialize Supabase client with session-scoped storage backend.
def get_supabase_client() -> Client:
    if "_supabase_client" not in st.session_state:
        storage = _build_auth_storage()
        st.session_state._supabase_client = create_client(
            SUPABASE_URL,
            SUPABASE_ANON_KEY,
            options=ClientOptions(
                flow_type="pkce",
                storage=storage,
            ),
        )
    return st.session_state._supabase_client


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
    if "guest_mode" not in st.session_state:
        st.session_state.guest_mode = False
    if "show_auth_form" not in st.session_state:
        st.session_state.show_auth_form = False
    if "auth_notice" not in st.session_state:
        st.session_state.auth_notice = ""

    if st.session_state.guest_mode:
        st.session_state.user = None
        return None

    if st.session_state.user:
        return st.session_state.user

    # Restore persisted Supabase session so browser refresh keeps users logged in.
    try:
        session_response = supabase.auth.get_session()
        session = getattr(session_response, "session", None)
        if session and getattr(session, "access_token", None):
            access_token = session.access_token
            refresh_token = getattr(session, "refresh_token", None)
            if refresh_token:
                supabase.auth.set_session(access_token, refresh_token)

            user = getattr(session, "user", None)
            if not user and access_token:
                user_response = supabase.auth.get_user(access_token)
                user = getattr(user_response, "user", None)

            if user:
                st.session_state.user = user
                st.session_state.access_token = access_token
                st.session_state.show_auth_form = False
                return user
    except Exception:
        # If restore fails, continue to explicit login UI.
        pass

    oauth_error = st.query_params.get("error")
    oauth_error_description = st.query_params.get("error_description")
    if oauth_error:
        st.session_state.auth_notice = (
            f"Login attempt failed: {oauth_error}"
            + (f" ({oauth_error_description})" if oauth_error_description else "")
        )
        st.query_params.clear()
        st.rerun()

    # Handle OAuth callback from Supabase (PKCE flow).
    auth_code = st.query_params.get("code")
    if auth_code:
        # If user is already authenticated, ignore stale callback codes from refresh.
        if st.session_state.get("user"):
            st.query_params.clear()
            st.rerun()

        try:
            response = supabase.auth.exchange_code_for_session({"auth_code": auth_code})
            if response and response.user:
                st.session_state.user = response.user
                st.session_state.access_token = response.session.access_token if response.session else None
                # Set the session on the Supabase client so RLS works
                if response.session:
                    supabase.auth.set_session(response.session.access_token, response.session.refresh_token)
                st.session_state.pop("oauth_url", None)
                st.query_params.clear()
                st.rerun()
            else:
                st.error("Login attempt failed: no user returned from Supabase callback.")
                st.session_state.pop("oauth_url", None)
                st.query_params.clear()
        except Exception as e:
            # A refresh can replay an already-consumed auth code; if a valid session exists,
            # continue silently instead of surfacing a false login failure.
            try:
                session_response = supabase.auth.get_session()
                session = getattr(session_response, "session", None)
                if session and getattr(session, "user", None):
                    st.session_state.user = session.user
                    st.session_state.access_token = getattr(session, "access_token", None)
                    st.session_state.pop("oauth_url", None)
                    st.query_params.clear()
                    st.rerun()
            except Exception:
                pass

            err_text = str(e)
            if "both auth code and code verifier should be non-empty" in err_text.lower():
                # PKCE verifier is no longer available (expired callback/reload).
                # Reset callback state and send user back to a fresh OAuth start.
                st.session_state.pop("oauth_url", None)
                st.session_state.pop("oauth_redirect_to", None)
                st.session_state.show_auth_form = True
                st.session_state.auth_notice = "Login link expired. Tap Google sign-in again."
                st.query_params.clear()
                st.rerun()

            st.error(f"Login attempt failed: {str(e)}")
            st.session_state.pop("oauth_url", None)
            st.query_params.clear()

    st.markdown(
        """
        <div style="text-align: center;">
            <h1>Portfolio brand.</h1>
            <div style="font-size: 18px; margin-bottom: 30px;">
                FLEX BUDGET
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    notice = str(st.session_state.get("auth_notice") or "").strip()
    if notice:
        st.warning(notice)
        st.session_state.auth_notice = ""

    landing_col1, landing_col2 = st.columns(2)
    with landing_col1:
        if st.button("Continue as Guest", key="continue_guest_btn", use_container_width=True):
            st.session_state.guest_mode = True
            st.session_state.show_auth_form = False
            for key in [
                "bills",
                "periods_order",
                "bill_catalog",
                "period_amount_cache",
                "cash_flow_by_period",
                "cash_flow_expressions",
            ]:
                st.session_state.pop(key, None)
            st.rerun()
    with landing_col2:
        if st.button("Sign In / Create Account", key="show_auth_btn", use_container_width=True):
            st.session_state.show_auth_form = True

    if not st.session_state.show_auth_form:
        st.info("Choose an option above to continue.")
        return None

    col_l, col_m, col_r = st.columns([1, 2, 1])
    with col_m:
        st.markdown("### Authentication")
        auth_tab1, auth_tab2, auth_tab3 = st.tabs(["Login", "Sign Up", "Google"])

        with auth_tab1:
            email = st.text_input("Email", key="login_email")
            password = st.text_input("Password", type="password", key="login_password")

            if st.button("Log In", key="login_button", use_container_width=True):
                try:
                    response = supabase.auth.sign_in_with_password(
                        {
                            "email": email,
                            "password": password,
                        }
                    )
                    st.session_state.user = response.user
                    st.session_state.access_token = response.session.access_token
                    # Set the session on the Supabase client so RLS works
                    supabase.auth.set_session(response.session.access_token, response.session.refresh_token)
                    st.rerun()
                except Exception as e:
                    st.error(f"Login attempt failed: {str(e)}")

        with auth_tab2:
            email = st.text_input("Email", key="signup_email")
            password = st.text_input("Password", type="password", key="signup_password")

            if st.button("Sign Up", key="signup_button", use_container_width=True):
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
                        # Set the session on the Supabase client so RLS works
                        supabase.auth.set_session(response.session.access_token, response.session.refresh_token)
                    st.success("Account created! Log in with your credentials.")
                except Exception as e:
                    st.error(f"Sign up failed: {str(e)}")

        with auth_tab3:
            # Skip button rendering during callback (callback handler processes it above)
            if not (st.query_params.get("code") or st.query_params.get("error")):
                st.info("Click the button below to sign in with Google")
                redirect_to = _resolve_oauth_redirect_url()
                
                # Cache the OAuth URL to avoid regenerating the PKCE verifier on each render
                should_refresh_oauth = (
                    "oauth_url" not in st.session_state
                    or st.session_state.get("oauth_redirect_to") != redirect_to
                )
                if should_refresh_oauth:
                    try:
                        response = supabase.auth.sign_in_with_oauth(
                            {
                                "provider": "google",
                                "options": {"redirect_to": redirect_to}
                            }
                        )
                        st.session_state.oauth_url = response.url if (response and hasattr(response, 'url')) else None
                        st.session_state.oauth_redirect_to = redirect_to
                    except Exception as e:
                        st.error(f"Google sign in error: {str(e)}")
                
                oauth_url = st.session_state.get("oauth_url")
                if oauth_url:
                    st.link_button("Sign In with Google", oauth_url, use_container_width=True)
                else:
                    st.error("Could not generate Google sign-in URL")

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


def period_display(value: str) -> str:
    normalized = normalize_period(value)
    if normalized.startswith("wk"):
        suffix = normalized[2:]
        if suffix.isdigit():
            return f"W{int(suffix)}"
    return normalized.upper()


# Supabase load functions (replaces CSV loading)
def load_bills_from_supabase(user_id: str) -> pd.DataFrame:
    """Load bills from Supabase for the authenticated user."""
    if not user_id:
        return DEFAULT_BILLS.copy()
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
    if not user_id:
        return DEFAULT_BILLS["bill"].astype(str).tolist()
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
    if not user_id:
        return defaults
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
    if not user_id:
        return
    try:
        # Fully sync bills so removed rows do not reappear on next load.
        supabase.table("bills").delete().eq("user_id", user_id).execute()
        if not bills_df.empty:
            bills_list = bills_df.to_dict(orient="records")
            for bill in bills_list:
                bill["user_id"] = user_id
            supabase.table("bills").insert(bills_list).execute()

        # Fully sync bill catalog so deletions persist.
        supabase.table("bill_catalog").delete().eq("user_id", user_id).execute()
        catalog_values = ensure_bill_catalog(bill_catalog)
        if catalog_values:
            catalog_list = [{"user_id": user_id, "bill": bill} for bill in catalog_values]
            supabase.table("bill_catalog").insert(catalog_list).execute()

        # Upsert cash flow (numeric values only).
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
    out.insert(0, "period", period_display(period))
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
    is_guest = bool(st.session_state.get("guest_mode"))
    if not user and not is_guest:
        st.stop()

    if is_guest:
        st.session_state.user = None
        st.session_state.access_token = None

    user_id = user.id if user else ""
    def reset_budget_state() -> None:
        """Drop cached budget data so mode changes do not leak prior user state."""
        for key in [
            "_guest_seeded",
            "bills",
            "periods_order",
            "bill_catalog",
            "period_amount_cache",
            "cash_flow_by_period",
            "cash_flow_expressions",
            "monthly_overview_week_enabled",
        ]:
            st.session_state.pop(key, None)

        # Clear dynamic per-period widget state that can otherwise survive mode switches.
        dynamic_prefixes = (
            "selected_bills_",
            "amount_input_",
            "cash_flow_expr_",
            "confirm_zero_",
            "new_bill_name_",
            "delete_bill_choice_",
            "confirm_delete_",
            "monthly_toggle_",
        )
        for key in list(st.session_state.keys()):
            if key.startswith(dynamic_prefixes):
                st.session_state.pop(key, None)

    st.markdown(
        """
        <style>
        /* Hide select clear controls (x) to avoid accidental full clears in bill pickers. */
        div[data-baseweb="select"] button[aria-label="Clear value"],
        div[data-baseweb="select"] button[aria-label="Clear all"],
        div[data-baseweb="select"] button[title="Clear value"],
        div[data-baseweb="select"] button[title="Clear all"] {
            display: none !important;
            visibility: hidden !important;
            width: 0 !important;
            min-width: 0 !important;
            padding: 0 !important;
            margin: 0 !important;
            border: 0 !important;
        }

        /* Make bill selector buttons less visually heavy. */
        div[data-testid="stHorizontalBlock"] button[kind="secondary"],
        div[data-testid="stHorizontalBlock"] button[kind="primary"] {
            padding-top: 0.2rem !important;
            padding-bottom: 0.2rem !important;
            min-height: 2rem !important;
        }

        /* Keep period tabs compact to avoid horizontal page scrolling. */
        div[data-testid="stTabs"] ul[role="tablist"] {
            gap: 0.2rem !important;
            flex-wrap: wrap !important;
            overflow-x: visible !important;
        }

        div[data-testid="stTabs"] div[data-baseweb="tab-list"] {
            gap: 0.2rem !important;
            flex-wrap: wrap !important;
            overflow-x: visible !important;
        }

        div[data-testid="stTabs"] button[role="tab"] {
            padding: 0.2rem 0.45rem !important;
            font-size: 0.82rem !important;
            min-height: 1.9rem !important;
            white-space: nowrap !important;
        }

        div[data-testid="stTabs"] button[data-baseweb="tab"] {
            padding: 0.2rem 0.45rem !important;
            font-size: 0.82rem !important;
            min-height: 1.9rem !important;
            white-space: nowrap !important;
            min-width: 0 !important;
        }

        /* Keep content constrained to viewport width. */
        [data-testid="stAppViewContainer"],
        .main,
        section.main {
            overflow-x: hidden !important;
        }

        /* Stack general multi-column rows on small screens to prevent overflow. */
        @media (max-width: 900px) {
            div[data-testid="stHorizontalBlock"]:not(:has(input[aria-label$="amount"])) {
                flex-wrap: wrap !important;
            }

            div[data-testid="stHorizontalBlock"]:not(:has(input[aria-label$="amount"])) > div[data-testid="column"] {
                min-width: 100% !important;
                flex: 1 1 100% !important;
            }
        }

        /* Keep tab buttons compact on narrow screens. */
        @media (max-width: 768px) {
            div[data-testid="stTabs"] button[role="tab"] {
                padding: 0.15rem 0.35rem !important;
                font-size: 0.75rem !important;
                min-height: 1.75rem !important;
            }

            div[data-testid="stTabs"] button[data-baseweb="tab"] {
                padding: 0.15rem 0.35rem !important;
                font-size: 0.75rem !important;
                min-height: 1.75rem !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    
    # Add logout button in sidebar
    with st.sidebar:
        if is_guest:
            st.caption("Guest mode")
            if st.button("Sign In", key="guest_to_login"):
                st.session_state.guest_mode = False
                st.session_state.show_auth_form = True
                reset_budget_state()
                st.rerun()
        else:
            if st.button("Use Guest Mode", key="switch_to_guest"):
                supabase.auth.sign_out()
                st.session_state.user = None
                st.session_state.access_token = None
                st.session_state.guest_mode = True
                st.session_state.show_auth_form = False
                st.session_state.pop("oauth_url", None)
                reset_budget_state()
                st.rerun()
            if st.button("Logout"):
                supabase.auth.sign_out()
                st.session_state.user = None
                st.session_state.access_token = None
                st.session_state.show_auth_form = False
                st.session_state.pop("oauth_url", None)
                reset_budget_state()
                st.rerun()

    header_col, calendar_col = st.columns([3, 2])
    with header_col:
        st.markdown(
            """
            <div>
                <h1 style='margin: 0; line-height: 1.05;'>Portfolio brand.</h1>
                <div style='font-size: 0.72rem;'>
                    FLEX BUDGET
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with calendar_col:
        st.subheader("Calendar")
        if "calendar_year" not in st.session_state or "calendar_month" not in st.session_state:
            today = app_today()
            st.session_state.calendar_year = today.year
            st.session_state.calendar_month = today.month

        nav_col1, nav_col2, nav_col3 = st.columns([1, 3, 1])
        with nav_col1:
            st.button("‹", key="calendar_prev_month", on_click=shift_calendar_month, args=(-1,))
        with nav_col2:
            st.caption(f"{calendar.month_name[st.session_state.calendar_month]} {st.session_state.calendar_year}")
        with nav_col3:
            st.button("›", key="calendar_next_month", on_click=shift_calendar_month, args=(1,))

        sunday_first_cal = calendar.Calendar(firstweekday=6)
        month_grid = sunday_first_cal.monthdayscalendar(st.session_state.calendar_year, st.session_state.calendar_month)
        month_df = pd.DataFrame(month_grid, columns=["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"])
        month_df = month_df.replace(0, "")
        month_df = month_df.astype(str)
        calendar_today = app_today()
        highlight_today = (
            st.session_state.calendar_year == calendar_today.year
            and st.session_state.calendar_month == calendar_today.month
        )
        header_html = "".join(
            [
                f"<th style='text-align:left;padding:4px 6px;border-bottom:1px solid #d0d7de;font-size:0.8rem;'>{col}</th>"
                for col in month_df.columns
            ]
        )
        row_html = ""
        for week_row in month_grid:
            row_cells: list[str] = []
            for day_value in week_row:
                if day_value == 0:
                    row_cells.append("<td style='text-align:left;padding:4px 6px;font-size:0.8rem;vertical-align:top;'></td>")
                    continue

                day_style = ""
                if highlight_today and day_value == calendar_today.day:
                    day_style = "background:rgba(220,38,38,0.22);border-radius:6px;"

                row_cells.append(
                    f"<td style='text-align:left;padding:4px 6px;font-size:0.8rem;vertical-align:top;{day_style}'>{day_value}</td>"
                )

            cells = "".join(row_cells)
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
    if is_guest:
        if not st.session_state.get("_guest_seeded"):
            st.session_state.bills = pd.DataFrame(columns=["period", "bill", "amount"])
            st.session_state.periods_order = WEEK_PERIODS.copy()
            st.session_state.bill_catalog = []
            st.session_state.period_amount_cache = build_period_amount_cache(st.session_state.bills)
            st.session_state.cash_flow_by_period = {period: 0.0 for period in WEEK_PERIODS}
            # Keep expressions in session state only (not persisted to DB)
            st.session_state.cash_flow_expressions = {period: "" for period in WEEK_PERIODS}
            st.session_state._guest_seeded = True
    else:
        st.session_state.pop("_guest_seeded", None)
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
        if "cash_flow_expressions" not in st.session_state:
            # Seed visible inputs from saved values so login/rerun does not zero them out.
            st.session_state.cash_flow_expressions = {
                period: f"{float(st.session_state.cash_flow_by_period.get(period, 0.0)):.2f}" for period in WEEK_PERIODS
            }

    periods = WEEK_PERIODS.copy()
    period_labels = {period: period_display(period) for period in periods}

    st.subheader("Bills by Period")
    st.caption("Open a week tab, add bills there, tap a bill name to select it, then delete if needed.")
    

    bill_catalog = ensure_bill_catalog(st.session_state.bill_catalog)
    if not bill_catalog and not is_guest:
        bill_catalog = ensure_bill_catalog(DEFAULT_BILLS["bill"].astype(str).tolist())
        st.session_state.bill_catalog = bill_catalog

    bill_tabs = st.tabs([period_labels[period] for period in periods])
    rebuilt_bills: list[pd.DataFrame] = []
    period_totals: dict[str, float] = {
        period: float(
            pd.to_numeric(
                st.session_state.bills[st.session_state.bills["period"] == period]["amount"],
                errors="coerce",
            ).fillna(0.0).sum()
        )
        for period in periods
    }
    for i, period in enumerate(periods):
        with bill_tabs[i]:
            period_label = period_labels[period]
            period_rows = st.session_state.bills[st.session_state.bills["period"] == period][["bill", "amount"]].copy()
            grouped = period_rows.groupby("bill", as_index=False)["amount"].sum() if not period_rows.empty else pd.DataFrame(columns=["bill", "amount"])
            existing_bill_set = set(grouped["bill"].astype(str).tolist()) if not grouped.empty else set()
            existing_bills = [bill for bill in bill_catalog if bill in existing_bill_set]

            if period not in st.session_state.period_amount_cache:
                st.session_state.period_amount_cache[period] = {}
            cache_for_period = st.session_state.period_amount_cache[period]
            if not grouped.empty:
                for row in grouped.itertuples(index=False):
                    bill_name = str(row.bill)
                    if bill_name not in cache_for_period:
                        cache_for_period[bill_name] = float(row.amount)

            selected_key = f"selected_bills_{period}"
            if selected_key not in st.session_state:
                st.session_state[selected_key] = existing_bills.copy()
            else:
                st.session_state[selected_key] = [bill for bill in st.session_state[selected_key] if bill in bill_catalog]

            selected_bills = st.session_state[selected_key]
            delete_select_key = f"delete_bill_choice_{period}"
            confirm_delete_key = f"confirm_delete_{period}"
            if st.session_state.get(delete_select_key) not in selected_bills:
                st.session_state.pop(delete_select_key, None)
                st.session_state.pop(confirm_delete_key, None)
            add_input_key = f"new_bill_name_{period}"
            controls_col, _ = st.columns([4, 3])
            with controls_col:
                with st.form(key=f"add_bill_form_{period}", clear_on_submit=True):
                    add_col1, add_col2, add_col3 = st.columns([3, 1, 1])
                    with add_col1:
                        new_bill_name = st.text_input(
                            f"Add bill to {period_label}",
                            key=add_input_key,
                            placeholder="internet",
                            label_visibility="collapsed",
                        )
                    with add_col2:
                        add_submitted = st.form_submit_button("Add")
                    with add_col3:
                        delete_requested = st.form_submit_button(
                            "Delete",
                            disabled=not bool(st.session_state.get(delete_select_key)),
                        )

            selected_bill_label = st.session_state.get(delete_select_key, "")
            st.caption(
                f"Selected: {selected_bill_label}" if selected_bill_label else "Tap a bill below to select it"
            )

            if add_submitted:
                candidate = str(new_bill_name).strip()
                if not candidate:
                    st.warning("Enter a bill name.")
                elif candidate in selected_bills:
                    st.info("Already in this week.")
                else:
                    st.session_state.bill_catalog = ensure_bill_catalog(st.session_state.bill_catalog + [candidate])
                    st.session_state[selected_key] = selected_bills + [candidate]
                    st.session_state.period_amount_cache[period][candidate] = float(
                        st.session_state.period_amount_cache[period].get(candidate, 0.0)
                    )
                    st.rerun()

            if delete_requested and st.session_state.get(delete_select_key):
                st.session_state[confirm_delete_key] = str(st.session_state[delete_select_key])
                st.rerun()

            if st.session_state.get(confirm_delete_key):
                candidate = str(st.session_state[confirm_delete_key])
                st.warning(f"Delete {candidate} from {period_label}?")
                confirm_col, cancel_col = st.columns(2)
                with confirm_col:
                    if st.button("Confirm delete", key=f"confirm_delete_btn_{period}", type="primary"):
                        st.session_state[selected_key] = [
                            current_bill for current_bill in st.session_state[selected_key] if current_bill != candidate
                        ]
                        st.session_state.period_amount_cache[period].pop(candidate, None)
                        st.session_state.pop(f"amount_input_{period}_{candidate}", None)
                        st.session_state.pop(delete_select_key, None)
                        st.session_state.pop(confirm_delete_key, None)
                        st.rerun()
                with cancel_col:
                    if st.button("Cancel", key=f"cancel_delete_btn_{period}"):
                        st.session_state.pop(confirm_delete_key, None)
                        st.rerun()

            selected_bills = st.session_state[selected_key]

            st.session_state.period_amount_cache[period] = {
                bill_name: float(cache_for_period.get(bill_name, 0.0)) for bill_name in selected_bills
            }

            period_entries: list[dict[str, float | str]] = []
            if selected_bills:
                for idx, bill_name in enumerate(selected_bills):
                    amount_key = f"amount_input_{period}_{bill_name}"
                    default_amount = float(st.session_state.period_amount_cache[period].get(bill_name, 0.0))
                    if amount_key not in st.session_state:
                        st.session_state[amount_key] = f"{default_amount:.2f}"

                    name_col, amount_col = st.columns([2, 3])
                    with name_col:
                        if st.button(
                            bill_name,
                            key=f"select_bill_{period}_{bill_name}",
                            type="primary" if st.session_state.get(delete_select_key) == bill_name else "secondary",
                            use_container_width=False,
                        ):
                            if st.session_state.get(delete_select_key) == bill_name:
                                st.session_state.pop(delete_select_key, None)
                            else:
                                st.session_state[delete_select_key] = bill_name
                            st.session_state.pop(confirm_delete_key, None)
                            st.rerun()

                    with amount_col:
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
            cash_flow_expr_key = f"cash_flow_expr_{period}"
            period_cash_flow = st.text_input(
                "Enter cash flow",
                value=st.session_state.cash_flow_expressions.get(period, ""),
                key=cash_flow_expr_key,
                placeholder="0.00",
            )
            st.session_state.cash_flow_expressions[period] = period_cash_flow
            period_cash_flow_value = parse_numeric_text(period_cash_flow, allow_expression=True)
            st.session_state.cash_flow_by_period[period] = period_cash_flow_value
            period_remaining = period_cash_flow_value - period_total
            period_totals[period] = period_total

            cue_color = "#15803d" if period_remaining >= 0 else "#b91c1c"
            cue_text = "positive" if period_remaining >= 0 else "negative"

            total_col, net_col = st.columns(2)
            with total_col:
                st.markdown(
                    f"<div style='font-size:0.78rem;color:#6b7280;font-weight:600;'>Total bills for {period_label}</div>",
                    unsafe_allow_html=True,
                )
                st.markdown(
                    f"<div style='font-size:1.05rem;font-weight:600;'>${period_total:,.2f}</div>",
                    unsafe_allow_html=True,
                )
            with net_col:
                st.markdown(
                    f"<div style='font-size:1.02rem;font-weight:700;'>Net cash flow for {period_label}</div>",
                    unsafe_allow_html=True,
                )
                st.markdown(
                    f"<div style='font-size:0.8rem;color:{cue_color};font-weight:600;'>Net cue: {cue_text}</div>",
                    unsafe_allow_html=True,
                )
                st.markdown(
                    f"<div style='font-size:1.6rem;font-weight:800;'>${period_remaining:,.2f}</div>",
                    unsafe_allow_html=True,
                )

            rebuilt_bills.append(cleaned_period)

    st.session_state.bills = clean_bills(
        pd.concat(rebuilt_bills, ignore_index=True) if rebuilt_bills else pd.DataFrame(columns=["period", "bill", "amount"])
    )
    st.session_state.bill_catalog = ensure_bill_catalog(
        bill_catalog + st.session_state.bills["bill"].astype(str).tolist()
    )

    with st.expander("Monthly Overview", expanded=False):
        st.caption("Combined totals for all weeks.")

        monthly_bills_total = sum(float(period_totals.get(period, 0.0)) for period in periods)
        monthly_cash_flow_total = sum(float(st.session_state.cash_flow_by_period.get(period, 0.0)) for period in periods)
        monthly_net = monthly_cash_flow_total - monthly_bills_total

        monthly_bills_col, monthly_cash_col, monthly_net_col = st.columns(3)
        monthly_bills_col.markdown(
            f"<div style='font-size:0.72rem;color:#6b7280;'>Bills</div><div style='font-size:1rem;font-weight:600;'>${monthly_bills_total:,.2f}</div>",
            unsafe_allow_html=True,
        )
        monthly_cash_col.markdown(
            f"<div style='font-size:0.72rem;color:#6b7280;'>Cash flow</div><div style='font-size:1rem;font-weight:600;'>${monthly_cash_flow_total:,.2f}</div>",
            unsafe_allow_html=True,
        )
        monthly_net_col.markdown(
            f"<div style='font-size:0.72rem;color:#6b7280;'>Net</div><div style='font-size:1rem;font-weight:600;'>${monthly_net:,.2f}</div>",
            unsafe_allow_html=True,
        )

    persist_to_supabase(user_id, st.session_state.bills, st.session_state.bill_catalog, st.session_state.cash_flow_by_period)

    st.session_state.periods_order = WEEK_PERIODS.copy()

if __name__ == "__main__":
    main()
