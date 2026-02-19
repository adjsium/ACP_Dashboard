"""
dashboard.py — ACP Coating Manufacturer Executive Dashboard
Streamlit app covering 3 requirements:
  1. Material requirements & next order date for open orders
  2. Scrap cost analysis (historical + open order)
  3. Customer ordering trend analysis
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta
import warnings

warnings.filterwarnings("ignore")

from utils import (
    load_all_data,
    build_material_requirements,
    build_historical_scrap_table,
    analyse_ordering_trend,
    project_next_order_date,
    roll_up_material,
)

# ─── PAGE CONFIG ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="ACP Executive Dashboard",
    page_icon="🏭",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .block-container { padding-top: 1.5rem; }
    div[data-testid="metric-container"] {
        border: 1px solid rgba(255,255,255,0.15);
        border-radius: 8px;
        padding: 0.8rem 1rem;
    }
    .stTabs [data-baseweb="tab"] { font-size: 15px; }
</style>
""", unsafe_allow_html=True)

# ─── LOAD DATA ────────────────────────────────────────────────────────────────
@st.cache_data
def get_data():
    df_inv, customer_data = load_all_data()
    inv_lookup = df_inv.set_index("No.").to_dict("index")

    # Record original BOM count per order BEFORE merging (used to detect multi-level)
    for order_no in customer_data:
        customer_data[order_no]["own_bom_count"] = len(customer_data[order_no]["boms"])
        customer_data[order_no]["own_boms"]      = dict(customer_data[order_no]["boms"])

    # Merge all BOM dicts across customers (for cross-sheet semi resolution)
    all_boms = {}
    for cdata in customer_data.values():
        all_boms.update(cdata["boms"])
    # Inject merged boms into each customer so roll-up can traverse cross-sheet
    for order_no in customer_data:
        customer_data[order_no]["boms"] = all_boms

    return df_inv, customer_data, inv_lookup, all_boms

df_inv, customer_data, inv_lookup, all_boms = get_data()
order_numbers = list(customer_data.keys())   # ['T040111', 'T018074']

# ─── SIDEBAR ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.image("https://img.icons8.com/color/96/factory.png", width=60)
    st.title("Dashboard Controls")
    st.divider()

    st.subheader("📅 Ship Dates for Open Orders")
    st.caption("Used to calculate the recommended base material order date "
               "(ship date − 14 days). Defaults to projected dates if not set.")

    ship_dates = {}
    for order_no in order_numbers:
        hist = customer_data[order_no]["history"]
        projected_ship, _ = project_next_order_date(hist)
        default_ship = projected_ship.date() if projected_ship else datetime.today().date()
        ship_dates[order_no] = st.date_input(
            f"Ship date — {order_no}",
            value=default_ship,
            help="Projected from average order interval if not known.",
        )

    st.divider()

# ─── HEADER ───────────────────────────────────────────────────────────────────
st.title("🏭 ACP Executive Dashboard")
st.divider()

# ─── KPI SUMMARY BAR ─────────────────────────────────────────────────────────
req_df, order_summary = build_material_requirements(customer_data, all_boms, inv_lookup)
scrap_df = build_historical_scrap_table(customer_data, inv_lookup, order_summary)

total_orders_at_risk   = int((req_df["Status"] == "🔴 Order Now").sum())
total_net_to_order_val = (
    req_df[req_df["Net to Order"] > 0].apply(
        lambda r: r["Net to Order"] * r["Unit Cost ($)"], axis=1
    ).sum()
)
total_scrap_historical = scrap_df[scrap_df["Order Type"] == "Historical"]["Scrap Cost ($)"].sum()
total_scrap_open       = scrap_df[scrap_df["Order Type"] == "Open Order"]["Scrap Cost ($)"].sum()

k2, k3, k4, k5 = st.columns(4)
k2.metric("🔴 Materials Need Re-Order", f"{total_orders_at_risk}",
          help="Base materials below gross requirement for orders requiring manufacturing")
k3.metric("💰 Est. Purchase Value Needed", f"${total_net_to_order_val:,.0f}",
          help="Net-to-order quantity × unit cost")
k4.metric("🗑 Historical Scrap Cost", f"${total_scrap_historical:,.2f}",
          help="Scrap cost of BASE MATRL items only across all historical orders")
k5.metric("⚠️ Open Order Scrap Estimate", f"${total_scrap_open:,.2f}",
          help="Estimated BASE MATRL scrap cost for manufacturing portion of open orders (after finished-goods offset)")

st.divider()

# ─── TABS ────────────────────────────────────────────────────────────────────
tab1, tab2, tab3 = st.tabs([
    "📦  Material Requirements & Order Dates",
    "💸  Scrap Cost Analysis",
    "📈  Customer Ordering Trends",
])


# ═════════════════════════════════════════════════════════════════════════════
# TAB 1 — MATERIAL REQUIREMENTS & NEXT ORDER DATE
# ═════════════════════════════════════════════════════════════════════════════
with tab1:
    st.header("📦 Material Requirements & Recommended Order Dates")


    # ── Per-order sections ────────────────────────────────────────────────────
    for order_no in order_numbers:
        cdata    = customer_data[order_no]
        qty_ord  = cdata["qty_ordered"]
        uom      = cdata["uom"]
        hist     = cdata["history"]
        os       = order_summary[order_no]

        # ── PO date reasoning ─────────────────────────────────────────────
        # Projected ship date comes from historical ordering intervals;
        # user can override it via the sidebar date picker.
        projected_ship, _ = project_next_order_date(hist)

        # Compute average interval for the reasoning text
        hist_clean = hist.dropna(subset=["Date"]).sort_values("Date")
        if len(hist_clean) >= 2:
            intervals = [
                (hist_clean["Date"].iloc[i] - hist_clean["Date"].iloc[i - 1]).days
                for i in range(1, len(hist_clean))
            ]
            avg_interval   = int(round(sum(intervals) / len(intervals)))
            last_ship_date = hist_clean["Date"].iloc[-1]
            interval_note  = (
                f"Based on **{len(hist_clean)} historical orders** "
                f"(avg interval **{avg_interval} days / "
                f"~{avg_interval // 30} months**), the projected next ship date "
                f"is **{projected_ship.strftime('%b %d, %Y') if projected_ship else 'N/A'}** "
                f"({avg_interval} days after last shipment on "
                f"**{last_ship_date.strftime('%b %d, %Y')}**)."
            )
        else:
            interval_note = (
                "⚠️ Insufficient order history to project a ship date automatically — "
                "please set the ship date manually in the sidebar."
            )

        # The actual ship date used (sidebar override or projected)
        ship_dt  = ship_dates[order_no]
        order_dt = pd.Timestamp(ship_dt) - timedelta(days=14)

        # Was the sidebar date changed from the projected default?
        if projected_ship and pd.Timestamp(ship_dt).date() != projected_ship.date():
            date_source = f"📝 **Manually set** in sidebar to **{pd.Timestamp(ship_dt).strftime('%b %d, %Y')}**."
        elif projected_ship:
            date_source = f"🔮 **Projected** from order history — defaulted to **{pd.Timestamp(ship_dt).strftime('%b %d, %Y')}**."
        else:
            date_source = f"📝 **Manually set** to **{pd.Timestamp(ship_dt).strftime('%b %d, %Y')}** (no history to project from)."

        st.subheader(f"Order: {order_no}  ·  Qty: {qty_ord:,.0f} {uom}")

        # ── PO date reasoning callout ─────────────────────────────────────
        with st.expander("📅 How is the Recommended PO Date calculated?", expanded=True):
            st.markdown(interval_note)
            st.markdown(date_source)
            st.markdown(
                f"**Recommended PO Date = Ship Date − 14 days** → "
                f"Place purchase order by **{order_dt.strftime('%b %d, %Y')}** "
                f"to allow 2 weeks lead time before shipment.  \n"
                f"*To use a different ship date, update it in the sidebar — "
                f"the PO date will recalculate automatically.*"
            )

        # ── Finished-goods stock offset banner ──────────────────────────
        fin_oh      = os["finished_on_hand"]
        net_to_make = os["net_to_manufacture"]

        if fin_oh > 0:
            if net_to_make == 0:
                st.success(
                    f"✅ **Finished goods fully cover this order.**  "
                    f"Stock on hand: **{fin_oh:,.2f} {uom}** vs order: "
                    f"**{qty_ord:,.0f} {uom}** — no raw material purchase needed."
                )
            else:
                st.warning(
                    f"⚠️ **Partial finished-goods offset.**  "
                    f"Stock on hand: **{fin_oh:,.2f} {uom}** — "
                    f"still need to manufacture **{net_to_make:,.0f} {uom}** "
                    f"from raw materials."
                )

        col_a, col_b, col_c, col_d = st.columns(4)
        col_a.metric("Order Qty",                f"{qty_ord:,.0f}")
        col_b.metric("Finished Goods On Hand",   f"{fin_oh:,.2f}")
        col_c.metric("Net Qty to Manufacture",   f"{net_to_make:,.0f}")
        col_d.metric("📅 Recommended PO Date",
                     order_dt.strftime("%b %d, %Y"),
                     help="Ship Date − 14 days. Change the ship date in the sidebar to update.")

        # ── Material table + chart: only needed when manufacturing is required ──
        if net_to_make > 0:
            sub_df = req_df[req_df["Order No."] == order_no].copy()
            sub_df = sub_df.drop(columns=["Order No.", "In Inventory"])

            def colour_status(val):
                if "🔴" in str(val):
                    return "color: #dc2626; font-weight: bold"
                elif "🟡" in str(val):
                    return "color: #d97706; font-weight: bold"
                return "color: #16a34a; font-weight: bold"

            # Format numbers: up to 2 dp, no trailing zeros (e.g. 2803.00 → 2,803)
            def fmt_num(v):
                try:
                    return f"{float(v):,.2f}".rstrip("0").rstrip(".")
                except (ValueError, TypeError):
                    return v

            num_cols = [c for c in ["Net Qty", "Gross Qty\n(w/ Scrap)", "Scrap Qty",
                                    "On Hand", "Net to Order", "Unit Cost ($)"]
                        if c in sub_df.columns]

            styled = (
                sub_df.style
                .applymap(colour_status, subset=["Status"])
                .format(fmt_num, subset=num_cols)
            )
            st.dataframe(styled, use_container_width=True, hide_index=True)

            # ── Chart: inventory-tracked materials only ───────────────────
            chart_df = req_df[
                (req_df["Order No."] == order_no) &
                (req_df["In Inventory"] == True)
            ].copy()

            if not chart_df.empty:
                fig = go.Figure()
                fig.add_bar(
                    name="On Hand",
                    x=chart_df["Material"],
                    y=chart_df["On Hand"],
                    marker_color="#3b82f6",
                    text=chart_df["On Hand"].apply(lambda v: f"{v:,.0f}"),
                    textposition="outside",
                )
                fig.add_bar(
                    name="Gross Required (w/ Scrap)",
                    x=chart_df["Material"],
                    y=chart_df["Gross Qty\n(w/ Scrap)"],
                    marker_color="#f87171",
                    text=chart_df["Gross Qty\n(w/ Scrap)"].apply(lambda v: f"{v:,.0f}"),
                    textposition="outside",
                )
                uom_label = chart_df["UOM"].iloc[0] if not chart_df.empty else "Units"
                fig.update_layout(
                    barmode="group",
                    title=f"{order_no} — Inventory-Tracked Materials: Stock vs. Requirement",
                    xaxis_title="Material / Product",
                    yaxis_title=f"Quantity ({uom_label})",
                    legend=dict(orientation="h", yanchor="bottom", y=1.02),
                    height=400,
                    plot_bgcolor="#f8fafc",
                )
                st.plotly_chart(fig, use_container_width=True)

        st.divider()

    # BOM roll-up tree (expandable)
    with st.expander("🌲 View Full Multi-Level BOM Roll-Up Detail"):
        # Only show orders whose OWN BOM sheet has more than 1 BOM
        # (T040111 has 1 BOM; T018074 has 4 — use own_bom_count saved before the merge)
        multi_level_orders = [
            o for o in order_numbers
            if customer_data[o].get("own_bom_count", 1) > 1
        ]
        if not multi_level_orders:
            st.info("No multi-level BOMs found for the current orders.")
        else:
            for order_no in multi_level_orders:
                st.markdown(f"#### {order_no}")
                # Use own_boms (original per-order BOMs, not the merged dict)
                own_boms = customer_data[order_no].get("own_boms", {})
                for bom_item, bom_df in own_boms.items():
                    st.markdown(f"**BOM → {bom_item}**")
                    st.dataframe(bom_df, use_container_width=True, hide_index=True)


# ═════════════════════════════════════════════════════════════════════════════
# TAB 2 — SCRAP COST ANALYSIS
# ═════════════════════════════════════════════════════════════════════════════
with tab2:
    st.header("💸 Scrap Cost Analysis — Historical & Open Orders")
    st.markdown(
        "⚠️ **Only `BASE MATRL` items from the Inventory Extract are included** — "
        "non-inventory and non-base-material components (e.g. POLY, MB, COR, PLUG) are excluded."
    )

    if scrap_df.empty:
        st.warning("No scrap data available — unit costs may be missing in inventory.")
    else:
        # ── Summary metrics: historical & open order scrap per order ─────────
        metric_cols = st.columns(len(order_numbers) * 2)
        for idx, order_no in enumerate(order_numbers):
            hist_val = scrap_df[
                (scrap_df["Order No."] == order_no) &
                (scrap_df["Order Type"] == "Historical")
            ]["Scrap Cost ($)"].sum()
            open_val = scrap_df[
                (scrap_df["Order No."] == order_no) &
                (scrap_df["Order Type"] == "Open Order")
            ]["Scrap Cost ($)"].sum()
            metric_cols[idx * 2].metric(
                f"🗑 {order_no} — Historical Scrap",
                f"${hist_val:,.2f}",
                help=f"Total base material scrap cost across all historical orders for {order_no}",
            )
            metric_cols[idx * 2 + 1].metric(
                f"⚠️ {order_no} — Open Order Scrap (Est.)",
                f"${open_val:,.2f}",
                help=f"Estimated base material scrap cost for the current open order of {order_no}",
            )

        st.markdown("---")

        # ── Per-order breakdown ───────────────────────────────────────────────
        for order_no in order_numbers:
            st.subheader(f"Order: {order_no}")
            sub = scrap_df[scrap_df["Order No."] == order_no].copy()

            # Per-order scrap metrics
            o_hist = sub[sub["Order Type"] == "Historical"]["Scrap Cost ($)"].sum()
            o_open = sub[sub["Order Type"] == "Open Order"]["Scrap Cost ($)"].sum()
            m1, m2 = st.columns(2)
            m1.metric("Historical Scrap",       f"${o_hist:,.2f}")
            m2.metric("Open Order Scrap (Est.)", f"${o_open:,.2f}")

            # Aggregate per SO Number (sum across materials)
            so_summary = (
                sub.groupby(["SO Number", "Date", "Order Type", "Qty Ordered"])["Scrap Cost ($)"]
                .sum()
                .reset_index()
                .sort_values("Date")
            )
            so_summary["Date_Label"] = so_summary["Date"].dt.strftime("%b %Y").fillna("Open")
            so_summary["Scrap Cost ($)"] = so_summary["Scrap Cost ($)"].round(2)

            col_l, col_r = st.columns([3, 2])

            with col_l:
                # Scrap cost by SO (bar chart)
                fig_bar = go.Figure()
                hist_mask = so_summary["Order Type"] == "Historical"
                open_mask = so_summary["Order Type"] == "Open Order"

                fig_bar.add_bar(
                    name="Historical",
                    x=so_summary.loc[hist_mask, "Date_Label"],
                    y=so_summary.loc[hist_mask, "Scrap Cost ($)"],
                    marker_color="#f59e0b",
                    text=so_summary.loc[hist_mask, "Scrap Cost ($)"].apply(lambda v: f"${v:,.0f}"),
                    textposition="outside",
                )
                if open_mask.any():
                    fig_bar.add_bar(
                        name="Open Order (Est.)",
                        x=["Open Order"],
                        y=so_summary.loc[open_mask, "Scrap Cost ($)"].values,
                        marker_color="#ef4444",
                        text=[f"${so_summary.loc[open_mask, 'Scrap Cost ($)'].values[0]:,.0f}"],
                        textposition="outside",
                    )
                fig_bar.update_layout(
                    title=f"{order_no} — Scrap Cost per Order",
                    yaxis_title="Scrap Cost ($)",
                    barmode="group",
                    height=340,
                    plot_bgcolor="#f8fafc",
                    legend=dict(orientation="h", yanchor="bottom", y=1.02),
                )
                st.plotly_chart(fig_bar, use_container_width=True)

            with col_r:
                # Scrap cost breakdown by material (pie) — open order only
                open_mat = sub[sub["Order Type"] == "Open Order"].groupby(
                    "Material")["Scrap Cost ($)"].sum().reset_index()
                open_mat = open_mat[open_mat["Scrap Cost ($)"] > 0]
                if not open_mat.empty:
                    fig_pie = px.pie(
                        open_mat,
                        names="Material",
                        values="Scrap Cost ($)",
                        title=f"{order_no} — Open Order Scrap by Material",
                        color_discrete_sequence=px.colors.sequential.RdBu,
                    )
                    fig_pie.update_traces(textinfo="percent+label")
                    fig_pie.update_layout(height=340)
                    st.plotly_chart(fig_pie, use_container_width=True)

            # Detailed table
            with st.expander(f"📋 Full Scrap Detail — {order_no}"):
                display_cols = ["SO Number", "Date", "Order Type", "Qty Ordered",
                                "Material", "Description", "UOM",
                                "Scrap Qty", "Unit Cost ($)", "Scrap Cost ($)"]
                detail = sub[display_cols].copy()
                detail["Date"] = detail["Date"].dt.strftime("%b %d, %Y").fillna("Open")
                st.dataframe(detail.sort_values(["SO Number", "Material"]),
                             use_container_width=True, hide_index=True)

            st.divider()



# ═════════════════════════════════════════════════════════════════════════════
# TAB 3 — CUSTOMER ORDERING TRENDS
# ═════════════════════════════════════════════════════════════════════════════
with tab3:
    st.header("📈 Customer Ordering Trends")

    # ── Fixed interpretations (analyst-reviewed) ─────────────────────────────
    TREND_LABELS = {
        "T040111": {
            "freq":   "Irregular, long gaps (avg ~2–3 yrs)",
            "volume": "↓ Slightly declining",
        },
        "T018074": {
            "freq":   "↑ Very active (avg ~5 months)",
            "volume": "→ Variable / high volume",
        },
    }

    trend_results = {}
    for order_no, cdata in customer_data.items():
        trend_results[order_no] = analyse_ordering_trend(
            cdata["history"], order_no
        )

    # ── Side-by-side customer summaries ──────────────────────────────────────
    cols = st.columns(len(order_numbers))
    for idx, order_no in enumerate(order_numbers):
        t = trend_results[order_no]
        if not t:
            continue
        labels = TREND_LABELS.get(order_no, {
            "freq":   t["freq_signal"],
            "volume": t["trend_signal"],
        })
        with cols[idx]:
            st.subheader(f"Customer: {order_no}")
            st.metric("Total Orders (History)", t["order_count"])
            st.metric("Avg Qty / Order",
                      f"{t['avg_qty_per_order']:,.0f} {t.get('uom','')}")
            st.metric("Avg Days Between Orders",
                      f"{t['avg_days_between_orders']:.0f} days")
            st.metric("Volume Trend",    labels["volume"])
            st.metric("Frequency Trend", labels["freq"])
            proj_s = t["projected_ship_date"].strftime("%b %d, %Y")
            proj_o = t["projected_order_date"].strftime("%b %d, %Y")
            st.metric("Projected Next Ship",      proj_s)
            st.metric("Projected Material Order", proj_o,
                      help="Projected ship date − 14 days")

    st.divider()

    # ── Per-customer detailed charts ──────────────────────────────────────────
    for order_no in order_numbers:
        t = trend_results[order_no]
        if not t:
            continue

        labels = TREND_LABELS.get(order_no, {
            "freq":   t["freq_signal"],
            "volume": t["trend_signal"],
        })

        st.subheader(f"📌 {order_no} — Order History Detail")

        # Interpretation callout
        st.info(
            f"**Frequency:** {labels['freq']}  \n"
            f"**Volume trend:** {labels['volume']}"
        )

        hist_df = t["history_df"].copy()
        hist_df["Order #"] = range(1, len(hist_df) + 1)

        # Trend line
        x_vals = hist_df["Order #"].values
        slope  = t["slope_qty"]
        avg_q  = t["avg_qty_per_order"]
        intercept = avg_q - slope * (len(x_vals) / 2)
        trend_y = slope * x_vals + intercept

        cl, cr = st.columns([3, 2])

        with cl:
            fig_vol = go.Figure()
            fig_vol.add_bar(
                name="Order Qty",
                x=hist_df["Date"].dt.strftime("%b %Y"),
                y=hist_df["Qty"],
                marker_color="#6366f1",
                text=hist_df["Qty"].apply(lambda v: f"{v:,.0f}"),
                textposition="outside",
            )
            fig_vol.add_scatter(
                name="Trend Line",
                x=hist_df["Date"].dt.strftime("%b %Y"),
                y=trend_y,
                mode="lines+markers",
                line=dict(color="#ef4444", dash="dash", width=2),
            )
            # Add open order indicator
            open_qty = customer_data[order_no]["qty_ordered"]
            proj_s_str = t["projected_ship_date"].strftime("%b %Y")
            fig_vol.add_scatter(
                name="Open Order (projected)",
                x=[proj_s_str],
                y=[open_qty],
                mode="markers",
                marker=dict(color="#f59e0b", size=14, symbol="star"),
            )
            fig_vol.update_layout(
                title=f"{order_no} — Quantity per Order Over Time",
                xaxis_title="Order Date",
                yaxis_title=f"Qty ({t.get('uom','')})",
                height=380,
                plot_bgcolor="#f8fafc",
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
            )
            st.plotly_chart(fig_vol, use_container_width=True)

        with cr:
            # Days between orders
            if len(t["intervals_days"]) > 0:
                gap_labels = [
                    f"SO{i+1}→SO{i+2}"
                    for i in range(len(t["intervals_days"]))
                ]
                fig_gap = go.Figure()
                fig_gap.add_bar(
                    name="Days Between Orders",
                    x=gap_labels,
                    y=t["intervals_days"],
                    marker_color="#34d399",
                    text=[f"{d:.0f}d" for d in t["intervals_days"]],
                    textposition="outside",
                )
                fig_gap.add_hline(
                    y=t["avg_days_between_orders"],
                    line_dash="dash", line_color="#ef4444",
                    annotation_text=f"Avg: {t['avg_days_between_orders']:.0f}d",
                )
                fig_gap.update_layout(
                    title=f"{order_no} — Days Between Orders",
                    yaxis_title="Days",
                    height=380,
                    plot_bgcolor="#f8fafc",
                )
                st.plotly_chart(fig_gap, use_container_width=True)

        # History table
        with st.expander(f"📋 Full Order History — {order_no}"):
            show_hist = hist_df.copy()
            show_hist["Date"] = show_hist["Date"].dt.strftime("%b %d, %Y")
            st.dataframe(show_hist[["SO_Number", "Date", "Qty", "UOM"]],
                         use_container_width=True, hide_index=True)

        st.divider()
