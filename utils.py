"""
utils.py — Shared data loading, BOM parsing, and calculation functions
for ACP Coating Manufacturer Executive Dashboard.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta

import os
FILE_PATH = os.path.join(os.path.dirname(__file__), "Items_Whitby_Extract.xlsx")


# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────

def parse_bom_blocks(bom_section: pd.DataFrame) -> dict:
    """
    Parse multiple BOM blocks from the K:P raw area of a customer sheet.
    Each block: [empty row] → [title row] → [header row] → [data rows…]

    Returns:
        dict: {item_no: DataFrame with columns
               [Type, No., Description, Quantity per, UOM, Scrap %]}
    """
    bom_dict = {}
    i = 0
    while i < len(bom_section):
        row = bom_section.iloc[i]
        non_null = row.dropna()

        # Title row = exactly 1 non-null cell
        if len(non_null) == 1 and i + 1 < len(bom_section):
            bom_title = str(non_null.iloc[0])

            # Extract item number from title
            # Format: "Edit - Production BOM - {ITEM_NO} ∙ {DESCRIPTION}"
            item_no = bom_title
            if "BOM - " in bom_title and " \u2219 " in bom_title:
                item_no = bom_title.split("BOM - ")[1].split(" \u2219 ")[0].strip()
            elif "BOM - " in bom_title:
                item_no = bom_title.split("BOM - ")[1].strip()

            header_row = bom_section.iloc[i + 1]
            if header_row.dropna().shape[0] >= 1:
                data_rows = []
                j = i + 2
                while j < len(bom_section):
                    data_row = bom_section.iloc[j]
                    if data_row.dropna().shape[0] == 0:
                        break
                    data_rows.append(data_row.tolist())
                    j += 1

                if data_rows:
                    bom_df = pd.DataFrame(data_rows)
                    bom_df = bom_df.iloc[:, :6]  # Ensure max 6 columns
                    bom_df.columns = ["Type", "No.", "Description",
                                      "Quantity per", "UOM", "Scrap %"]
                    bom_df.dropna(how="all", inplace=True)
                    bom_df.reset_index(drop=True, inplace=True)
                    bom_df["Quantity per"] = pd.to_numeric(
                        bom_df["Quantity per"], errors="coerce").fillna(0)
                    bom_df["Scrap %"] = pd.to_numeric(
                        bom_df["Scrap %"], errors="coerce").fillna(0)
                    bom_df["No."] = bom_df["No."].astype(str).str.strip()
                    bom_dict[item_no] = bom_df
                i = j + 1
            else:
                i += 1
        else:
            i += 1

    return bom_dict


def load_all_data():
    """
    Load and parse all three sheets from the Excel file.

    Returns:
        df_inv       : DataFrame — Tab 1 inventory extract
        customer_data: dict     — keyed by order_no, each value contains:
                                  'order', 'history', 'boms', 'sheet_name'
    """
    xl = pd.ExcelFile(FILE_PATH)

    # ── TAB 1: Inventory ───────────────────────────────────────────────────
    df_inv = pd.read_excel(FILE_PATH, sheet_name=0, header=2, usecols="B:J")
    df_inv.dropna(how="all", inplace=True)
    df_inv.reset_index(drop=True, inplace=True)
    df_inv["No."] = df_inv["No."].astype(str).str.strip()
    df_inv["Unit Cost"] = pd.to_numeric(df_inv["Unit Cost"], errors="coerce").fillna(0)
    df_inv["Quantity on Hand"] = pd.to_numeric(
        df_inv["Quantity on Hand"], errors="coerce").fillna(0)

    # ── TABS 2 & 3: Customer order data ────────────────────────────────────
    customer_data = {}
    for sheet_idx in [1, 2]:
        sheet_name = xl.sheet_names[sheet_idx]
        raw = pd.read_excel(FILE_PATH, sheet_name=sheet_idx, header=None)

        # Order section (B3:D)
        order_header = raw.iloc[2, 1:4].tolist()
        order_data = raw.iloc[3:, 1:4].copy()
        order_data.columns = order_header
        order_data = order_data.dropna(how="all").reset_index(drop=True)

        # Order history (F3:I)
        hist_header = raw.iloc[2, 5:9].tolist()
        hist_data = raw.iloc[3:, 5:9].copy()
        hist_data.columns = hist_header
        hist_data = hist_data.dropna(how="all").reset_index(drop=True)
        hist_data.columns = ["SO_Number", "Date", "Qty", "UOM"]
        hist_data["SO_Number"] = hist_data["SO_Number"].astype(str)
        hist_data["Qty"] = pd.to_numeric(hist_data["Qty"], errors="coerce").fillna(0)
        # Date format in source data: "Mar 25/2016", "Apr 10/2019" → "%b %d/%Y"
        hist_data["Date"] = pd.to_datetime(
            hist_data["Date"], format="%b %d/%Y", errors="coerce")
        hist_data.sort_values("Date", inplace=True)
        hist_data.reset_index(drop=True, inplace=True)

        # BOM blocks (K:P)
        bom_area = raw.iloc[:, 10:16].copy()
        bom_area.reset_index(drop=True, inplace=True)
        bom_dict = parse_bom_blocks(bom_area)

        order_no = str(order_data.iloc[0]["No."]).strip() if not order_data.empty else sheet_name
        qty_ordered = float(order_data.iloc[0]["Qty Ordered"]) if not order_data.empty else 0
        uom = str(order_data.iloc[0]["UOM"]) if not order_data.empty else ""

        customer_data[order_no] = {
            "sheet_name": sheet_name,
            "order_no": order_no,
            "qty_ordered": qty_ordered,
            "uom": uom,
            "order": order_data,
            "history": hist_data,
            "boms": bom_dict,
        }

    return df_inv, customer_data


# ─────────────────────────────────────────────────────────────────────────────
# BOM ROLL-UP ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def roll_up_material(item_no: str, qty: float, bom_dict: dict,
                     include_scrap: bool = True, _visited: set = None) -> dict:
    """
    Recursively roll up a multi-level BOM to raw/base-material quantities.

    For each semi-finished component found in bom_dict, the function recurses.
    Items not found in bom_dict are treated as purchased/raw materials.

    Args:
        item_no      : Top-level finished item number
        qty          : Quantity of the top-level item to produce
        bom_dict     : Combined BOM dictionary from all sheets
        include_scrap: Whether to apply Scrap % at each level
        _visited     : Internal set to guard against circular references

    Returns:
        dict: {material_no: total_qty_needed}
    """
    if _visited is None:
        _visited = set()
    if item_no in _visited:
        return {}
    _visited = _visited | {item_no}  # immutable copy per branch

    # Base case: no BOM for this item → it is a raw/purchased material
    if item_no not in bom_dict:
        return {item_no: qty}

    result = {}
    for _, comp in bom_dict[item_no].iterrows():
        comp_no = str(comp["No."]).strip()
        qty_per = float(comp["Quantity per"])
        scrap_pct = float(comp["Scrap %"]) if include_scrap else 0.0

        # Gross quantity of this component required for `qty` units of parent
        child_qty = qty * qty_per * (1 + scrap_pct / 100)

        if comp_no in bom_dict:
            # Semi-finished: recurse
            sub = roll_up_material(comp_no, child_qty, bom_dict,
                                   include_scrap, _visited)
            for mat, mat_qty in sub.items():
                result[mat] = result.get(mat, 0) + mat_qty
        else:
            # Raw material leaf node
            result[comp_no] = result.get(comp_no, 0) + child_qty

    return result


def build_material_requirements(customer_data: dict, bom_dict: dict,
                                 inv_lookup: dict) -> tuple:
    """
    For every open order, calculate gross & net material requirements
    and compare against current inventory.

    Finished-goods offset:  if the finished product itself appears in
    inv_lookup (e.g. T040111 has 296.99 MSF on hand), only the delta
    qty needs to be manufactured from raw materials.

    Returns:
        (req_df, order_summary)
        req_df       – DataFrame, one row per (order, material)
                       columns: Order No., Material, Description, UOM,
                                Unit Cost ($), In Inventory,
                                Net Qty, Gross Qty (w/ Scrap), Scrap Qty,
                                On Hand, Net to Order, Status
        order_summary – dict  keyed by order_no:
                         qty_ordered, uom, finished_on_hand,
                         net_to_manufacture
    """
    rows = []
    order_summary = {}

    for order_no, cdata in customer_data.items():
        qty_ordered = cdata["qty_ordered"]
        uom         = cdata["uom"]
        all_boms    = cdata["boms"]
        all_boms.update(bom_dict)

        # ── Finished-goods stock offset ───────────────────────────────────
        fin_inv          = inv_lookup.get(order_no, {})
        finished_on_hand = float(fin_inv.get("Quantity on Hand", 0))
        net_to_manufacture = max(qty_ordered - finished_on_hand, 0)

        order_summary[order_no] = {
            "order_no":           order_no,
            "qty_ordered":        qty_ordered,
            "uom":                uom,
            "finished_on_hand":   finished_on_hand,
            "net_to_manufacture": net_to_manufacture,
        }

        # ── BOM roll-up based on net qty to manufacture ───────────────────
        gross = roll_up_material(order_no, net_to_manufacture, all_boms,
                                 include_scrap=True)
        net   = roll_up_material(order_no, net_to_manufacture, all_boms,
                                 include_scrap=False)

        # If no manufacturing needed, still show inventory-tracked BOM
        # materials with gross = 0 so the table stays informative
        if not gross:
            gross_ref = roll_up_material(order_no, 1, all_boms, include_scrap=False)
            gross = {mat: 0.0 for mat in gross_ref}
            net   = {mat: 0.0 for mat in gross_ref}

        for mat_no, gross_qty in gross.items():
            net_qty      = net.get(mat_no, gross_qty)
            scrap_qty    = gross_qty - net_qty
            inv          = inv_lookup.get(mat_no, {})
            in_inventory = mat_no in inv_lookup
            on_hand      = float(inv.get("Quantity on Hand", 0))
            unit_cost    = float(inv.get("Unit Cost", 0))
            description  = inv.get("Description", mat_no)
            uom_mat      = inv.get("Base Unit of Measure", "")
            net_to_order = max(gross_qty - on_hand, 0)

            if gross_qty == 0:
                # No manufacturing needed — stock covers the order
                status = "🟢 Covered by Stock"
            elif net_to_order > 0:
                status = "🔴 Order Now"
            elif on_hand < gross_qty * 1.15:
                status = "🟡 Watch"
            else:
                status = "🟢 Sufficient"

            rows.append({
                "Order No.":              order_no,
                "Material":               mat_no,
                "Description":            description,
                "UOM":                    uom_mat,
                "Unit Cost ($)":          round(unit_cost, 4),
                "In Inventory":           in_inventory,
                "Net Qty":                round(net_qty, 2),
                "Gross Qty\n(w/ Scrap)":  round(gross_qty, 2),
                "Scrap Qty":              round(scrap_qty, 2),
                "On Hand":                round(on_hand, 2),
                "Net to Order":           round(net_to_order, 2),
                "Status":                 status,
            })

    return pd.DataFrame(rows), order_summary


# ─────────────────────────────────────────────────────────────────────────────
# SCRAP COST CALCULATIONS
# ─────────────────────────────────────────────────────────────────────────────

def calculate_scrap_cost_per_order(order_no: str, qty: float,
                                    bom_dict: dict, inv_lookup: dict) -> pd.DataFrame:
    """
    Calculate the scrap cost (by material) for a given order quantity.

    Scrap cost = (Gross qty - Net qty) × Unit Cost

    Returns a DataFrame: material, description, scrap_qty, unit_cost, scrap_cost
    """
    gross = roll_up_material(order_no, qty, bom_dict, include_scrap=True)
    net   = roll_up_material(order_no, qty, bom_dict, include_scrap=False)

    rows = []
    for mat_no, gross_qty in gross.items():
        inv = inv_lookup.get(mat_no, {})

        # Only include BASE MATRL items that are tracked in the inventory extract
        if not inv or inv.get("Item Category Code", "") != "BASE MATRL":
            continue

        net_qty   = net.get(mat_no, gross_qty)
        scrap_qty = gross_qty - net_qty
        if scrap_qty <= 0:
            continue

        unit_cost   = float(inv.get("Unit Cost", 0))
        description = inv.get("Description", mat_no)
        uom         = inv.get("Base Unit of Measure", "")

        rows.append({
            "Material":       mat_no,
            "Description":    description,
            "UOM":            uom,
            "Scrap Qty":      round(scrap_qty, 4),
            "Unit Cost ($)":  round(unit_cost, 4),
            "Scrap Cost ($)": round(scrap_qty * unit_cost, 2),
        })

    return pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["Material", "Description", "UOM",
                 "Scrap Qty", "Unit Cost ($)", "Scrap Cost ($)"])


def build_historical_scrap_table(customer_data: dict,
                                  inv_lookup: dict,
                                  order_summary: dict = None) -> pd.DataFrame:
    """
    Calculate scrap cost for every historical order + the current open order.

    open order qty uses net_to_manufacture from order_summary if provided
    (i.e. if finished goods already cover the order, open order scrap = 0).

    Returns a flat DataFrame with one row per (SO, material).
    """
    rows = []
    for order_no, cdata in customer_data.items():
        bom_dict = cdata["boms"]

        # Historical orders — always use actual shipped qty
        for _, hist_row in cdata["history"].iterrows():
            qty   = float(hist_row["Qty"])
            date  = hist_row["Date"]
            so_no = hist_row["SO_Number"]

            df_sc = calculate_scrap_cost_per_order(
                order_no, qty, bom_dict, inv_lookup)
            for _, sc_row in df_sc.iterrows():
                rows.append({
                    "Order No.":      order_no,
                    "SO Number":      so_no,
                    "Date":           date,
                    "Order Type":     "Historical",
                    "Qty Ordered":    qty,
                    "Material":       sc_row["Material"],
                    "Description":    sc_row["Description"],
                    "UOM":            sc_row["UOM"],
                    "Scrap Qty":      sc_row["Scrap Qty"],
                    "Unit Cost ($)":  sc_row["Unit Cost ($)"],
                    "Scrap Cost ($)": sc_row["Scrap Cost ($)"],
                })

        # Open order — use net_to_manufacture (account for finished goods)
        if order_summary and order_no in order_summary:
            open_qty = order_summary[order_no]["net_to_manufacture"]
        else:
            open_qty = cdata["qty_ordered"]

        df_sc = calculate_scrap_cost_per_order(
            order_no, open_qty, bom_dict, inv_lookup)
        for _, sc_row in df_sc.iterrows():
            rows.append({
                "Order No.":      order_no,
                "SO Number":      "OPEN",
                "Date":           pd.NaT,
                "Order Type":     "Open Order",
                "Qty Ordered":    open_qty,
                "Material":       sc_row["Material"],
                "Description":    sc_row["Description"],
                "UOM":            sc_row["UOM"],
                "Scrap Qty":      sc_row["Scrap Qty"],
                "Unit Cost ($)":  sc_row["Unit Cost ($)"],
                "Scrap Cost ($)": sc_row["Scrap Cost ($)"],
            })

    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ─────────────────────────────────────────────────────────────────────────────
# ORDERING TREND ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────

def analyse_ordering_trend(history: pd.DataFrame, order_no: str) -> dict:
    """
    Analyse order history for frequency and volume trends.

    Returns a dict with:
        - order_count
        - total_qty
        - avg_qty_per_order
        - date_range
        - avg_days_between_orders
        - projected_next_order_date
        - slope_qty      (positive = growing volume per order)
        - trend_signal   ("↑ Increasing", "→ Stable", "↓ Decreasing")
        - freq_signal    ("↑ More Frequent", "→ Stable", "↓ Less Frequent")
    """
    if history.empty or history["Date"].isna().all():
        return {}

    df = history.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)
    if len(df) < 2:
        return {}

    order_count = len(df)
    total_qty   = df["Qty"].sum()
    avg_qty     = df["Qty"].mean()

    # Days between consecutive orders
    intervals_days = [
        (df["Date"].iloc[i] - df["Date"].iloc[i - 1]).days
        for i in range(1, len(df))
    ]
    avg_interval = np.mean(intervals_days)

    # Projected next ship date
    last_date = df["Date"].iloc[-1]
    projected_ship = last_date + timedelta(days=avg_interval)
    projected_order_date = projected_ship - timedelta(days=14)

    # Linear regression on quantity over time (numeric index as x)
    x = np.arange(order_count)
    y = df["Qty"].values.astype(float)
    if len(x) >= 2:
        slope, intercept = np.polyfit(x, y, 1)
    else:
        slope = 0.0

    # Trend signal based on slope relative to avg
    pct_slope = slope / avg_qty if avg_qty != 0 else 0
    if pct_slope > 0.05:
        trend_signal = "↑ Increasing"
    elif pct_slope < -0.05:
        trend_signal = "↓ Decreasing"
    else:
        trend_signal = "→ Stable"

    # Frequency signal: compare first-half vs second-half intervals
    mid = len(intervals_days) // 2
    if mid > 0:
        early_avg = np.mean(intervals_days[:mid])
        late_avg  = np.mean(intervals_days[mid:])
        freq_pct  = (early_avg - late_avg) / early_avg if early_avg != 0 else 0
        if freq_pct > 0.15:
            freq_signal = "↑ More Frequent"
        elif freq_pct < -0.15:
            freq_signal = "↓ Less Frequent"
        else:
            freq_signal = "→ Stable"
    else:
        freq_signal = "→ Stable"

    date_range = f"{df['Date'].iloc[0].strftime('%b %Y')} – {df['Date'].iloc[-1].strftime('%b %Y')}"

    return {
        "order_no":                 order_no,
        "order_count":              order_count,
        "total_qty":                total_qty,
        "avg_qty_per_order":        round(avg_qty, 1),
        "uom":                      df["UOM"].iloc[0] if "UOM" in df.columns else "",
        "date_range":               date_range,
        "avg_days_between_orders":  round(avg_interval, 0),
        "intervals_days":           intervals_days,
        "projected_ship_date":      projected_ship,
        "projected_order_date":     projected_order_date,
        "slope_qty":                round(slope, 2),
        "trend_signal":             trend_signal,
        "freq_signal":              freq_signal,
        "history_df":               df,
    }


def project_next_order_date(history: pd.DataFrame) -> tuple:
    """
    Returns (projected_ship_date, recommended_order_date = ship_date - 14 days)
    based on average interval between historical orders.
    """
    df = history.dropna(subset=["Date"]).sort_values("Date")
    if len(df) < 2:
        return None, None
    intervals = [
        (df["Date"].iloc[i] - df["Date"].iloc[i - 1]).days
        for i in range(1, len(df))
    ]
    avg_interval = np.mean(intervals)
    last_date    = df["Date"].iloc[-1]
    ship_date    = last_date + timedelta(days=avg_interval)
    order_date   = ship_date - timedelta(days=14)
    return ship_date, order_date
