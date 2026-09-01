# Flex Budget

A multi-user Streamlit app for weekly budget planning with real-time Supabase sync.

## Features

- Weekly budget planning with flexible 5-week cycles
- Real-time Supabase sync
- Email/password and Google OAuth login
- Multi-user access with RLS-backed data separation
- Receipt uploads: attach an image/PDF to any bill via Supabase Storage

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

3. In the Supabase dashboard for the project in `SUPABASE_URL`, run
   `supabase/receipts_migration.sql` in SQL Editor. The script creates the
   `receipts` table, RLS policies, and a private `receipts` Storage bucket; it
   is safe to rerun after a partial execution.

4. Run locally:

   ```powershell
   streamlit run app.py
   ```

## Google Sign-In Setup

1. In Google Cloud Console, create OAuth 2.0 Web application credentials. Add
   `https://<project-ref>.supabase.co/auth/v1/callback` as an authorized redirect URI.
2. In Supabase Dashboard, open Authentication > Providers > Google, enable it,
   and enter the Google client ID and client secret.
3. In Supabase Dashboard, open Authentication > URL Configuration and add your
   deployed Streamlit URL (for example `https://pb-flexbudget.streamlit.app`) to
   the Redirect URLs allow list. Add `http://localhost:8501` for local testing.
4. Optionally set `OAUTH_REDIRECT_URL` in Streamlit secrets to the deployed app
   URL. Without it, the app derives the current host automatically.

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
