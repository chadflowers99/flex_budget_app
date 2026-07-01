# Cash Flow

A multi-user Streamlit app for weekly budget planning with real-time Supabase sync.

## Features

- **Weekly Budget Planning**: Organize bills into flexible 5-week planning cycles
- **Real-time Sync**: Automatic cloud synchronization with Supabase
- **Multi-User**: Team collaboration with row-level security (RLS)
- **Authentication**: Email/password and Google OAuth login
- **Cash Flow Projections**: Track income, expenses, and running balance

## Quick Start

1. Install dependencies:

   ```powershell
   pip install -r requirements.txt
   ```

2. Set up environment:
   - Create `.streamlit/secrets.toml` with Supabase credentials
   - Get your credentials from [Supabase Dashboard](https://supabase.com)

3. Run:

   ```powershell
   streamlit run app.py
   ```

## Deployment

Deployed to Streamlit Cloud: [pb-budget.streamlit.app](https://pb-budget.streamlit.app)
