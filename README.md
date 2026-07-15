# Flex Budget

A multi-user Streamlit app for weekly budget planning with real-time Supabase sync.

## Features

- Weekly budget planning with flexible 5-week cycles
- Real-time Supabase sync
- Email/password and Google OAuth login
- Multi-user access with RLS-backed data separation

## Quick Start

1. Install dependencies:

   ```powershell
   pip install -r requirements.txt
   ```

2. Create local secrets file at `.streamlit/secrets.toml` with:

   ```toml
   SUPABASE_URL = "https://<your-project>.supabase.co"
   SUPABASE_ANON_KEY = "<your-anon-key>"
   ```

3. Run locally:

   ```powershell
   streamlit run app.py
   ```

## Streamlit Deployment Runbook

1. App source settings:
   - Repo: `chadflowers99/flex_budget_app`
   - Branch: `main`
   - Main file: `app.py`
2. App URL:
   - `https://pb-flexbudget.streamlit.app`
3. Streamlit Cloud secrets:
   - `SUPABASE_URL`
   - `SUPABASE_ANON_KEY`
4. Supabase Auth redirect configuration:
   - `https://pb-flexbudget.streamlit.app`
   - `https://pb-marketholdings.streamlit.app`
   - Optional local dev: `http://localhost:8501`
