# Flexible Budget App

A Streamlit app for flexible budgeting where bill due dates can shift between pay periods.

## Features

- Edit bills and income directly in app tables.
- Move bills between periods by changing the period value.
- Automatic period summary: income, expenses, net, running balance.
- Save and load bills/income to CSV.

## Quick Start

1. Install dependencies:

   ```powershell
   pip install -r requirements.txt
   ```

2. Run:

   ```powershell
   streamlit run app.py
   ```

## Data Files

- `bills.csv`
- `income.csv`

If files do not exist, the app starts with defaults and can create them when you click Save.
