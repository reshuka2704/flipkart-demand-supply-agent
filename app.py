from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st
from google import genai
from google.genai import types

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

st.set_page_config(
    page_title="Demand-to-Supply Impact Agent",
    page_icon="📦",
    layout="wide",
)


@st.cache_data(show_spinner=False)
def load_data(data_dir: str) -> dict[str, pd.DataFrame]:
    """Load the corrected synthetic Excel workbook and required sheets."""
    root = Path(data_dir)
    workbook = root / "flipkart_demand_to_supply_impact_dataset_corrected.xlsx"
    if not workbook.exists():
        raise FileNotFoundError(
            f"Corrected workbook not found: {workbook}. Keep the corrected .xlsx file inside data/."
        )
    required_sheets = [
        "Historical_Orders", "Products", "Current_Inventory", "Suppliers",
        "Fulfilment_Capacity", "Campaign_Scenarios", "Purchase_Orders",
    ]
    excel = pd.ExcelFile(workbook)
    missing = [sheet for sheet in required_sheets if sheet not in excel.sheet_names]
    if missing:
        raise ValueError("Corrected workbook is missing sheet(s): " + ", ".join(missing))
    tables = {sheet: pd.read_excel(workbook, sheet_name=sheet) for sheet in required_sheets}
    tables["Historical_Orders"]["Date"] = pd.to_datetime(
        tables["Historical_Orders"]["Date"], errors="coerce"
    )
    for column in ["Order_Date", "Expected_Delivery_Date", "Actual_Delivery_Date"]:
        if column in tables["Purchase_Orders"].columns:
            tables["Purchase_Orders"][column] = pd.to_datetime(
                tables["Purchase_Orders"][column], errors="coerce"
            )
    return tables


def build_scenario(data: dict[str, pd.DataFrame], uplift_percent: float) -> dict[str, Any]:
    """Recalculate inventory, supplier and fulfilment risk for a demand uplift.

    The underlying records are synthetic and are used for a learning prototype.
    Risk thresholds are explicit project assumptions, not Flipkart policy.
    """
    uplift_percent = float(max(0, min(100, uplift_percent)))
    uplift = uplift_percent / 100.0

    orders = data["Historical_Orders"].copy()
    products = data["Products"].copy()
    inventory = data["Current_Inventory"].copy()
    suppliers = data["Suppliers"].copy()
    fc_capacity = data["Fulfilment_Capacity"].copy()

    # A baseline daily demand estimate for each product at each fulfilment centre.
    sku_fc_demand = (
        orders.groupby(["FC_ID", "SKU_ID"], as_index=False)["Orders"]
        .mean()
        .rename(columns={"Orders": "Baseline_Daily_Demand"})
    )
    inv = (
        inventory.merge(sku_fc_demand, on=["FC_ID", "SKU_ID"], how="left")
        .merge(products, on="SKU_ID", how="left")
        .merge(suppliers, on="Supplier_ID", how="left")
    )
    inv["Baseline_Daily_Demand"] = inv["Baseline_Daily_Demand"].fillna(0.0)
    inv["Scenario_Daily_Demand"] = inv["Baseline_Daily_Demand"] * (1 + uplift)
    inv["Stock_Cover_Days"] = inv.apply(
        lambda r: (r["Available_Stock"] / r["Scenario_Daily_Demand"])
        if r["Scenario_Daily_Demand"] > 0 else float("inf"), axis=1
    )
    inv["Lead_Time_Demand"] = inv["Scenario_Daily_Demand"] * inv["Lead_Time_Days"]
    inv["Potential_Inventory_Gap"] = (
        inv["Lead_Time_Demand"] - inv["Available_Stock"]
    ).clip(lower=0)

    def inventory_risk(row: pd.Series) -> str:
        if row["Scenario_Daily_Demand"] <= 0:
            return "No Demand"
        if row["Stock_Cover_Days"] < row["Lead_Time_Days"]:
            return "High Risk"
        if row["Stock_Cover_Days"] < row["Lead_Time_Days"] + 3:
            return "Medium Risk"
        return "Low Risk"

    inv["Inventory_Risk"] = inv.apply(inventory_risk, axis=1)
    inv = inv.merge(fc_capacity[["FC_ID", "FC_Name"]], on="FC_ID", how="left")

    # Supplier demand is summed over all products supplied by that supplier.
    orders_with_supplier = orders.merge(products[["SKU_ID", "Supplier_ID"]], on="SKU_ID", how="left")
    daily_supplier = (
        orders_with_supplier.groupby(["Date", "Supplier_ID"], as_index=False)["Orders"]
        .sum()
    )
    supplier_demand = (
        daily_supplier.groupby("Supplier_ID", as_index=False)["Orders"]
        .mean()
        .rename(columns={"Orders": "Baseline_Daily_Demand"})
    )
    sup = suppliers.merge(supplier_demand, on="Supplier_ID", how="left")
    sup["Baseline_Daily_Demand"] = sup["Baseline_Daily_Demand"].fillna(0.0)
    sup["Scenario_Daily_Demand"] = sup["Baseline_Daily_Demand"] * (1 + uplift)
    sup["Capacity_Utilisation_Pct"] = sup.apply(
        lambda r: r["Scenario_Daily_Demand"] / r["Capacity_Per_Day"] * 100
        if r["Capacity_Per_Day"] > 0 else 0.0, axis=1
    )
    sup["Capacity_Gap"] = (
        sup["Scenario_Daily_Demand"] - sup["Capacity_Per_Day"]
    ).clip(lower=0)

    def supplier_risk(row: pd.Series) -> str:
        if row["Capacity_Gap"] > 0:
            return "High Risk"
        if row["Capacity_Utilisation_Pct"] >= 90 or row["OTIF_Percentage"] < 90:
            return "Medium Risk"
        return "Low Risk"

    sup["Supplier_Risk"] = sup.apply(supplier_risk, axis=1)

    # FC capacity uses the current daily order baseline provided in the capacity file.
    fc = fc_capacity.copy()
    fc["Scenario_Daily_Orders"] = fc["Current_Daily_Orders"] * (1 + uplift)
    fc["Capacity_Utilisation_Pct"] = fc.apply(
        lambda r: r["Scenario_Daily_Orders"] / r["Practical_Daily_Capacity"] * 100
        if r["Practical_Daily_Capacity"] > 0 else 0.0, axis=1
    )
    fc["Capacity_Gap"] = (
        fc["Scenario_Daily_Orders"] - fc["Practical_Daily_Capacity"]
    ).clip(lower=0)
    fc["FC_Risk"] = fc["Capacity_Utilisation_Pct"].apply(
        lambda x: "High Risk" if x > 100 else ("Medium Risk" if x >= 90 else "Low Risk")
    )

    high_inv = int((inv["Inventory_Risk"] == "High Risk").sum())
    med_inv = int((inv["Inventory_Risk"] == "Medium Risk").sum())
    high_sup = int((sup["Supplier_Risk"] == "High Risk").sum())
    high_fc = int((fc["FC_Risk"] == "High Risk").sum())

    if high_fc > 0 or high_sup > 0 or high_inv >= max(1, int(len(inv) * 0.25)):
        readiness = "High Risk"
        readiness_reason = "At least one major capacity/supplier issue or a large share of inventory is below lead-time coverage."
    elif med_inv > 0 or (sup["Supplier_Risk"] == "Medium Risk").any() or (fc["FC_Risk"] == "Medium Risk").any():
        readiness = "Elevated Risk"
        readiness_reason = "There are medium-risk inventory, supplier, or fulfilment indicators to monitor."
    else:
        readiness = "Relatively Ready"
        readiness_reason = "No major risk was flagged by the prototype's rules; this is not a service guarantee."

    # Keep outputs in plain Python types so they can be returned to Gemini as tool results.
    inv_cols = ["FC_Name", "Category", "SKU_ID", "Product_Name", "Supplier_ID",
                "Available_Stock", "Scenario_Daily_Demand", "Stock_Cover_Days",
                "Lead_Time_Days", "Potential_Inventory_Gap", "Inventory_Risk"]
    sup_cols = ["Supplier_ID", "Supplier_Name", "Scenario_Daily_Demand", "Capacity_Per_Day",
                "Capacity_Utilisation_Pct", "Capacity_Gap", "OTIF_Percentage", "Supplier_Risk"]
    fc_cols = ["FC_ID", "FC_Name", "Scenario_Daily_Orders", "Practical_Daily_Capacity",
               "Capacity_Utilisation_Pct", "Capacity_Gap", "Avg_Processing_Time_Hours", "SLA_Hours", "FC_Risk"]

    inv_sorted = inv.sort_values(["Inventory_Risk", "Potential_Inventory_Gap"], ascending=[True, False])
    inv_highest = inv.sort_values("Potential_Inventory_Gap", ascending=False)
    sup_sorted = sup.sort_values("Capacity_Utilisation_Pct", ascending=False)
    fc_sorted = fc.sort_values("Capacity_Utilisation_Pct", ascending=False)

    def records(frame: pd.DataFrame, cols: list[str], n: int) -> list[dict[str, Any]]:
        out = frame[cols].head(n).copy()
        out = out.replace([float("inf"), float("-inf")], None).where(pd.notna(out), None)
        for col in out.select_dtypes(include="number").columns:
            out[col] = out[col].round(2)
        return out.to_dict(orient="records")

    return {
        "uplift_percent": uplift_percent,
        "overall_readiness": readiness,
        "readiness_reason": readiness_reason,
        "inventory_summary": {
            "store_sku_combinations": int(len(inv)),
            "high_risk_items": high_inv,
            "medium_risk_items": med_inv,
            "low_risk_items": int((inv["Inventory_Risk"] == "Low Risk").sum()),
            "total_potential_inventory_gap_units": round(float(inv["Potential_Inventory_Gap"].sum()), 2),
        },
        "supplier_summary": {
            "supplier_count": int(len(sup)),
            "high_risk_suppliers": high_sup,
            "medium_risk_suppliers": int((sup["Supplier_Risk"] == "Medium Risk").sum()),
        },
        "fulfilment_summary": {
            "fc_count": int(len(fc)),
            "high_risk_fcs": high_fc,
            "max_utilisation_pct": round(float(fc["Capacity_Utilisation_Pct"].max()), 2),
        },
        "top_inventory_gaps": records(inv_highest, inv_cols, 8),
        "inventory_risk_examples": records(inv_sorted, inv_cols, 8),
        "supplier_risks": records(sup_sorted, sup_cols, 12),
        "fulfilment_centre_risks": records(fc_sorted, fc_cols, 4),
        "assumptions": [
            "All supplied operational records are synthetic and are not actual Flipkart data.",
            "Historical order averages estimate product/FC daily demand; demand is increased uniformly by the selected uplift.",
            "FC capacity uses Current_Daily_Orders from Fulfilment_Capacity.csv as the base.",
            "Inventory high risk means stock cover is below supplier lead time; medium risk means cover is within three additional days.",
            "FC risk is high above 100% capacity utilisation and medium from 90% to 100%; supplier risk is high when scenario demand exceeds daily capacity, or medium near capacity / OTIF below 90%.",
            "These are prototype rules for demonstration, not official Flipkart thresholds or forecasts."
        ],
    }


# Load input tables once. If data is missing, stop with a clear, actionable error.
try:
    DATA = load_data(str(DATA_DIR))
except Exception as exc:
    st.error(f"Could not load the dataset. {exc}")
    st.stop()


# The tool exposed to Gemini is a real Python calculation, not a model-generated guess.
def analyze_supply_chain(uplift_percent: float) -> dict[str, Any]:
    """Calculate the supply-chain readiness scenario for a demand uplift.

    Args:
        uplift_percent: Percentage uplift in demand, between 0 and 100.

    Returns:
        Scenario KPIs, fulfilment-centre risk, supplier risk and inventory gaps.
    """
    return build_scenario(DATA, uplift_percent)


def get_saved_api_key() -> str:
    """Read the key from Streamlit Secrets or an environment variable."""
    try:
        key = st.secrets.get("GEMINI_API_KEY", "")
        if key:
            return str(key).strip()
    except Exception:
        pass
    return os.getenv("GEMINI_API_KEY", "").strip()


def get_saved_model() -> str:
    """Read an optional Gemini model override from Secrets or environment."""
    try:
        model = st.secrets.get("GEMINI_MODEL", "")
        if model:
            return str(model).strip()
    except Exception:
        pass
    return os.getenv("GEMINI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def ask_gemini(question: str, history: list[dict[str, str]], uplift_percent: int, api_key: str, model: str) -> str:
    """Call Gemini and allow it to invoke the Python scenario-analysis function."""
    client = genai.Client(api_key=api_key)
    history_text = "\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in history[-8:]
    )
    prompt = f"""Current dashboard demand uplift: {uplift_percent}%.
Previous conversation (may be empty):
{history_text}

User's latest question: {question}

You are advising a supply-chain manager using a synthetic, Flipkart-inspired prototype dataset.
Use the Python analyze_supply_chain tool whenever numerical scenario results, bottlenecks, or a different uplift are required. Do not invent numbers. If the user names a different uplift, use that uplift in the tool call. Clearly distinguish calculated results from assumptions, explain the most important risk first, and recommend practical actions such as inventory rebalancing, replenishment prioritisation, supplier follow-up, or workload shifting only where supported by the results.
Never imply this is real Flipkart internal data. Keep the answer clear and interview-friendly.
"""
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[analyze_supply_chain],
            temperature=0.2,
            max_output_tokens=1200,
        ),
    )
    answer = getattr(response, "text", None)
    if not answer:
        raise RuntimeError("Gemini returned an empty response. Check the model name and API access.")
    return answer.strip()


# ----------------------------- UI -------------------------------------------
st.title("📦 Demand-to-Supply Impact Agent")
st.caption("Flipkart-inspired festive demand scenario prototype · Corrected synthetic Excel dataset")

with st.sidebar:
    st.header("Scenario controls")
    preset = st.selectbox(
        "Choose demand scenario",
        ["Custom uplift", "Moderate surge · 15%", "Big sale · 30%", "Extreme surge · 50%"],
        index=2,
    )
    if preset == "Custom uplift":
        uplift_percent = st.slider(
            "Demand uplift (%)", min_value=0, max_value=100, value=30, step=5,
            help="Applies the same demand uplift to the synthetic historical demand baseline.",
        )
    else:
        uplift_percent = {
            "Moderate surge · 15%": 15,
            "Big sale · 30%": 30,
            "Extreme surge · 50%": 50,
        }[preset]
        st.metric("Selected demand uplift", f"+{uplift_percent}%")
    st.divider()
    st.header("Gemini API")
    api_key = get_saved_api_key()
    if not api_key:
        api_key = st.text_input(
            "Gemini API key", type="password", placeholder="Paste key for this session",
            help="This input is not written to the source code. For a deployed app, use Streamlit Secrets.",
        ).strip()
    model = st.text_input("Model", value=get_saved_model(), help="Default is gemini-3.8-flash. Change only if your API key does not have access.").strip()
    if api_key:
        st.success("API key configured for this session")
    else:
        st.info("Dashboard works without the API key. Gemini chat needs a key.")
    st.caption("Source: data/flipkart_demand_to_supply_impact_dataset_corrected.xlsx · Synthetic data, not actual Flipkart operating data")

scenario = build_scenario(DATA, uplift_percent)
inv_df = pd.DataFrame(scenario["top_inventory_gaps"])
# For dashboard charts/tables, retain the full scenario data by rebuilding the row-level views.
# (The same function applies the defined scenario rules consistently.)

# Rebuild table views for richer filtering and charting.
orders = DATA["Historical_Orders"]
products = DATA["Products"]
inventory = DATA["Current_Inventory"]
suppliers = DATA["Suppliers"]
fc_base = DATA["Fulfilment_Capacity"]
base_sku_fc = orders.groupby(["FC_ID", "SKU_ID"], as_index=False)["Orders"].mean().rename(columns={"Orders": "Baseline_Daily_Demand"})
full_inv = inventory.merge(base_sku_fc, on=["FC_ID", "SKU_ID"], how="left").merge(products, on="SKU_ID", how="left").merge(suppliers, on="Supplier_ID", how="left").merge(fc_base[["FC_ID", "FC_Name"]], on="FC_ID", how="left")
full_inv["Scenario_Daily_Demand"] = full_inv["Baseline_Daily_Demand"].fillna(0) * (1 + uplift_percent / 100)
full_inv["Stock_Cover_Days"] = full_inv.apply(lambda r: r["Available_Stock"] / r["Scenario_Daily_Demand"] if r["Scenario_Daily_Demand"] > 0 else float("inf"), axis=1)
full_inv["Potential_Inventory_Gap"] = (full_inv["Scenario_Daily_Demand"] * full_inv["Lead_Time_Days"] - full_inv["Available_Stock"]).clip(lower=0)
full_inv["Inventory_Risk"] = full_inv.apply(lambda r: "No Demand" if r["Scenario_Daily_Demand"] <= 0 else ("High Risk" if r["Stock_Cover_Days"] < r["Lead_Time_Days"] else ("Medium Risk" if r["Stock_Cover_Days"] < r["Lead_Time_Days"] + 3 else "Low Risk")), axis=1)

# Supplier and FC output tables for the charts.
ord_sup = orders.merge(products[["SKU_ID", "Supplier_ID"]], on="SKU_ID", how="left")
daily_sup = ord_sup.groupby(["Date", "Supplier_ID"], as_index=False)["Orders"].sum()
sup_demand = daily_sup.groupby("Supplier_ID", as_index=False)["Orders"].mean().rename(columns={"Orders": "Baseline_Daily_Demand"})
full_sup = suppliers.merge(sup_demand, on="Supplier_ID", how="left")
full_sup["Scenario_Daily_Demand"] = full_sup["Baseline_Daily_Demand"].fillna(0) * (1 + uplift_percent / 100)
full_sup["Capacity_Utilisation_Pct"] = full_sup["Scenario_Daily_Demand"] / full_sup["Capacity_Per_Day"] * 100
full_sup["Capacity_Gap"] = (full_sup["Scenario_Daily_Demand"] - full_sup["Capacity_Per_Day"]).clip(lower=0)
full_sup["Supplier_Risk"] = full_sup.apply(lambda r: "High Risk" if r["Capacity_Gap"] > 0 else ("Medium Risk" if r["Capacity_Utilisation_Pct"] >= 90 or r["OTIF_Percentage"] < 90 else "Low Risk"), axis=1)
full_fc = fc_base.copy()
full_fc["Scenario_Daily_Orders"] = full_fc["Current_Daily_Orders"] * (1 + uplift_percent / 100)
full_fc["Capacity_Utilisation_Pct"] = full_fc["Scenario_Daily_Orders"] / full_fc["Practical_Daily_Capacity"] * 100
full_fc["Capacity_Gap"] = (full_fc["Scenario_Daily_Orders"] - full_fc["Practical_Daily_Capacity"]).clip(lower=0)
full_fc["FC_Risk"] = full_fc["Capacity_Utilisation_Pct"].apply(lambda x: "High Risk" if x > 100 else ("Medium Risk" if x >= 90 else "Low Risk"))

# KPI row
k1, k2, k3, k4 = st.columns(4)
k1.metric("Demand scenario", f"+{uplift_percent}%")
k2.metric("High-risk fulfilment centres", f"{(full_fc['FC_Risk'] == 'High Risk').sum()} / {len(full_fc)}")
k3.metric("High-risk suppliers", f"{(full_sup['Supplier_Risk'] == 'High Risk').sum()} / {len(full_sup)}")
k4.metric("High-risk SKU–FC combinations", f"{(full_inv['Inventory_Risk'] == 'High Risk').sum()} / {len(full_inv)}")

readiness_col, reason_col = st.columns([1, 3])
readiness_col.subheader(f"Overall readiness: {scenario['overall_readiness']}")
reason_col.write(scenario["readiness_reason"])
st.caption("Prototype rule-based status, not an official Flipkart threshold or service-level guarantee.")
st.divider()

fc_tab, sup_tab, inv_tab, chat_tab = st.tabs(["Fulfilment capacity", "Supplier readiness", "Inventory coverage", "Ask the AI agent"])

with fc_tab:
    st.subheader("Can fulfilment centres absorb the demand increase?")
    fc_plot = full_fc.melt(
        id_vars=["FC_Name"],
        value_vars=["Scenario_Daily_Orders", "Practical_Daily_Capacity"],
        var_name="Measure", value_name="Orders per day",
    )
    fig = px.bar(fc_plot, x="FC_Name", y="Orders per day", color="Measure", barmode="group", title="Scenario orders vs. practical capacity")
    st.plotly_chart(fig, use_container_width=True)
    fc_show = full_fc[["FC_Name", "Current_Daily_Orders", "Scenario_Daily_Orders", "Practical_Daily_Capacity", "Capacity_Utilisation_Pct", "Capacity_Gap", "Avg_Processing_Time_Hours", "SLA_Hours", "FC_Risk"]].copy()
    fc_show["Capacity_Utilisation_Pct"] = fc_show["Capacity_Utilisation_Pct"].round(1)
    st.dataframe(fc_show, use_container_width=True, hide_index=True)
    st.download_button("Download fulfilment scenario CSV", fc_show.to_csv(index=False).encode("utf-8"), "fulfilment_scenario.csv", "text/csv")

with sup_tab:
    st.subheader("Supplier demand vs. daily capacity")
    sup_plot = full_sup.sort_values("Capacity_Utilisation_Pct", ascending=False).copy()
    sup_plot["Capacity_Utilisation_Pct"] = sup_plot["Capacity_Utilisation_Pct"].round(1)
    fig = px.bar(sup_plot, x="Supplier_Name", y="Capacity_Utilisation_Pct", color="Supplier_Risk", title="Estimated supplier capacity utilisation (%)")
    fig.add_hline(y=100, line_dash="dash", annotation_text="100% capacity")
    st.plotly_chart(fig, use_container_width=True)
    sup_show = sup_plot[["Supplier_ID", "Supplier_Name", "Scenario_Daily_Demand", "Capacity_Per_Day", "Capacity_Utilisation_Pct", "Capacity_Gap", "OTIF_Percentage", "Lead_Time_Days", "Supplier_Risk"]].copy()
    sup_show["Scenario_Daily_Demand"] = sup_show["Scenario_Daily_Demand"].round(1)
    sup_show["Capacity_Utilisation_Pct"] = sup_show["Capacity_Utilisation_Pct"].round(1)
    st.dataframe(sup_show, use_container_width=True, hide_index=True)
    st.download_button("Download supplier scenario CSV", sup_show.to_csv(index=False).encode("utf-8"), "supplier_scenario.csv", "text/csv")

with inv_tab:
    st.subheader("Where might inventory run short before replenishment arrives?")
    f1, f2 = st.columns(2)
    category_opts = ["All"] + sorted(full_inv["Category"].dropna().astype(str).unique().tolist())
    fc_opts = ["All"] + sorted(full_inv["FC_Name"].dropna().astype(str).unique().tolist())
    chosen_category = f1.selectbox("Category", category_opts)
    chosen_fc = f2.selectbox("Fulfilment centre", fc_opts)
    filtered = full_inv.copy()
    if chosen_category != "All":
        filtered = filtered[filtered["Category"] == chosen_category]
    if chosen_fc != "All":
        filtered = filtered[filtered["FC_Name"] == chosen_fc]
    risk_counts = full_inv["Inventory_Risk"].value_counts().rename_axis("Inventory risk").reset_index(name="SKU–FC combinations")
    fig = px.bar(risk_counts, x="Inventory risk", y="SKU–FC combinations", title="Inventory risk distribution")
    st.plotly_chart(fig, use_container_width=True)
    inv_show = filtered[["FC_Name", "Category", "Product_Name", "SKU_ID", "Supplier_Name", "Available_Stock", "Scenario_Daily_Demand", "Stock_Cover_Days", "Lead_Time_Days", "Potential_Inventory_Gap", "Inventory_Risk"]].copy()
    for col in ["Scenario_Daily_Demand", "Stock_Cover_Days", "Potential_Inventory_Gap"]:
        inv_show[col] = inv_show[col].replace([float("inf"), float("-inf")], pd.NA).round(2)
    st.dataframe(inv_show.sort_values(["Inventory_Risk", "Potential_Inventory_Gap"], ascending=[True, False]), use_container_width=True, hide_index=True)
    st.download_button("Download inventory scenario CSV", inv_show.to_csv(index=False).encode("utf-8"), "inventory_scenario.csv", "text/csv")

with chat_tab:
    st.subheader("Ask questions about the scenario")
    st.write("Example: **At a 45% demand increase, which bottlenecks should management address first?**")
    if "chat_messages" not in st.session_state:
        st.session_state.chat_messages = []
    for msg in st.session_state.chat_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
    question = st.chat_input("Ask about demand surges, capacity, inventory gaps or supplier risks…")
    if question:
        st.session_state.chat_messages.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)
        if not api_key:
            answer = "Add your Gemini API key in the sidebar, or configure `GEMINI_API_KEY` in Streamlit Secrets, to enable the AI chat. The scenario dashboard itself works without the API."
            st.session_state.chat_messages.append({"role": "assistant", "content": answer})
            with st.chat_message("assistant"):
                st.markdown(answer)
        else:
            try:
                with st.chat_message("assistant"):
                    with st.spinner("Analysing scenario with Gemini…"):
                        answer = ask_gemini(question, st.session_state.chat_messages[:-1], uplift_percent, api_key, model or DEFAULT_MODEL)
                    st.markdown(answer)
                st.session_state.chat_messages.append({"role": "assistant", "content": answer})
            except Exception as exc:
                error_message = (
                    "The Gemini API call failed. Check that your key is valid, billing/quota is available, "
                    "and the selected model is enabled for your key. Details: " + str(exc)
                )
                st.session_state.chat_messages.append({"role": "assistant", "content": error_message})
                st.error(error_message)
    if st.button("Clear chat history"):
        st.session_state.chat_messages = []
        st.rerun()

with st.expander("How this prototype calculates risk"):
    st.markdown(
        """
        - **Scenario demand:** baseline daily demand × (1 + demand uplift).
        - **Inventory cover:** available stock ÷ scenario daily demand.
        - **Inventory gap:** max(lead-time demand − available stock, 0).
        - **Supplier utilisation:** scenario supplier demand ÷ supplier daily capacity.
        - **Fulfilment utilisation:** scenario daily orders ÷ practical daily capacity.
        - **Risk thresholds:** inventory high if stock cover is below lead time; FC high above 100% utilisation; supplier high when scenario demand exceeds capacity. These are demonstration rules and should be calibrated with real business owners before operational use.
        """
    )
    st.write("Source workbook: data/flipkart_demand_to_supply_impact_dataset_corrected.xlsx. The app reads the corrected workbook sheets directly. All records are synthetic.")
