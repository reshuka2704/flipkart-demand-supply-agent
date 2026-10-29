# Demand-to-Supply Impact Agent — Streamlit prototype

A learning prototype for a **Flipkart-inspired** festive demand-surge scenario. It estimates inventory coverage, supplier capacity, and fulfilment-centre capacity, then connects Gemini to a Python analysis function so users can ask scenario questions in natural language.

**Dataset source:** `data/flipkart_demand_to_supply_impact_dataset_corrected.xlsx`. The app reads this corrected workbook directly, including `Historical_Orders`, `Products`, `Current_Inventory`, `Suppliers`, `Purchase_Orders`, `Fulfilment_Capacity`, and `Campaign_Scenarios`.

**Important:** All operational records are synthetic. This is not connected to Flipkart systems or internal data. Risk thresholds are prototype assumptions, not official Flipkart rules.

## Project structure

```text
flipkart_demand_streamlit/
├── app.py
├── requirements.txt
├── README.md
├── .gitignore
├── .streamlit/secrets.toml.example
└── data/flipkart_demand_to_supply_impact_dataset_corrected.xlsx
```

## 1. Run locally

1. Install Python 3.10 or later.
2. Extract this project and open a terminal in the `flipkart_demand_streamlit` folder.
3. Create a virtual environment: `python -m venv .venv`
4. Activate it on Windows: `.venv\\Scripts\\activate` (macOS/Linux: `source .venv/bin/activate`).
5. Install dependencies: `pip install -r requirements.txt`
6. Configure your Gemini API key below.
7. Run: `streamlit run app.py`

## 2. Configure Gemini

For local use, copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`, then replace the placeholder with your actual key. The `.gitignore` excludes the real secrets file. Alternatively, paste the key into the app's sidebar for the current session only.

```toml
GEMINI_API_KEY = "YOUR_ACTUAL_GEMINI_API_KEY"
GEMINI_MODEL = "gemini-3.8-flash"
```

Never commit your real API key to GitHub.

## 3. Upload to GitHub

1. Create a repository, for example `flipkart-demand-supply-agent`.
2. Upload the **contents** of this project folder, not the ZIP file itself.
3. Ensure the repository includes `app.py`, `requirements.txt`, `README.md`, `.gitignore`, `.streamlit/secrets.toml.example`, and the `data/` folder with the corrected workbook.
4. Do not upload `.streamlit/secrets.toml` or any real API key.

Optional Git commands from the project folder:

```bash
git init
git add app.py requirements.txt README.md .gitignore .streamlit/secrets.toml.example data/flipkart_demand_to_supply_impact_dataset_corrected.xlsx
git commit -m "Add demand-to-supply Streamlit prototype"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/flipkart-demand-supply-agent.git
git push -u origin main
```

Replace `YOUR-USERNAME` with your GitHub username and use your actual repository URL.

## 4. Deploy from GitHub with Streamlit Community Cloud

1. Open https://share.streamlit.io/ and sign in with GitHub.
2. Create an app, select your repository, branch `main`, and main file path `app.py`.
3. In app settings, open **Secrets** and add:

```toml
GEMINI_API_KEY = "YOUR_ACTUAL_GEMINI_API_KEY"
GEMINI_MODEL = "gemini-3.8-flash"
```

4. Save/deploy. If already deployed, reboot after changing secrets.
5. Test the dashboard and ask: `At a 45% demand increase, which bottlenecks should management address first?`

The dashboard calculations work without Gemini, but AI chat needs a valid API key and available model access.

## Features

- Adjustable demand uplift from 0% to 100%, with quick scenario presets.
- Fulfilment-centre orders vs. practical capacity.
- Supplier demand vs. daily capacity and OTIF.
- Inventory stock cover and estimated lead-time gaps.
- CSV downloads for scenario tables.
- Gemini chat with Python function calling for numerical scenario analysis.

All figures are prototype calculations from synthetic data, not actual Flipkart operational results.
