import pandas as pd
import numpy as np

file_path = "/Users/leonardowu/ACP_Dashboard/Items_Whitby_Extract.xlsx"

xl = pd.ExcelFile(file_path)
print("=" * 70)
print(f"SHEET NAMES: {xl.sheet_names}")
print("=" * 70)

# ─────────────────────────────────────────────────────────────────────────────
# TAB 1: INVENTORY STOCK LEVEL
# Header at row 3 (0-indexed = 2), columns B:J (0-indexed = 1:9)
# ─────────────────────────────────────────────────────────────────────────────
print("\n\n" + "=" * 70)
print("TAB 1: INVENTORY STOCK LEVEL")
print("=" * 70)

df_inv_raw = pd.read_excel(file_path, sheet_name=0, header=None)
print(f"\n[RAW SHAPE] {df_inv_raw.shape} rows x cols")

# Show rows 0-5, cols B:J (indices 1-9) to confirm header row
print("\n[RAW PREVIEW rows 0-7, cols B:J]:")
print(df_inv_raw.iloc[0:8, 1:10].to_string())

# Read with header at row 3 (index=2), cols B:J
df_inv = pd.read_excel(
    file_path,
    sheet_name=0,
    header=2,
    usecols="B:J"
)
df_inv.dropna(how='all', inplace=True)
df_inv.reset_index(drop=True, inplace=True)

print(f"\n[SHAPE] {df_inv.shape}")
print(f"\n[COLUMNS]\n{df_inv.columns.tolist()}")
print(f"\n[DTYPES]\n{df_inv.dtypes}")
print(f"\n[ALL DATA]\n{df_inv.to_string()}")


# ─────────────────────────────────────────────────────────────────────────────
# TABS 2 & 3: CUSTOMER ORDER DATA
# ─────────────────────────────────────────────────────────────────────────────
for sheet_idx in [1, 2]:
    sheet_name = xl.sheet_names[sheet_idx]
    print("\n\n" + "=" * 70)
    print(f"TAB {sheet_idx + 1}: {sheet_name}")
    print("=" * 70)

    # Read entire raw sheet (no header)
    raw = pd.read_excel(file_path, sheet_name=sheet_idx, header=None)
    print(f"\n[RAW SHAPE] {raw.shape} rows x cols")

    # Show full raw layout (rows 0-10, all cols) to understand structure
    print(f"\n[RAW PREVIEW rows 0-15, all cols]:")
    print(raw.iloc[0:16, :].to_string())

    # ── ORDER SECTION: B3:D (cols 1-3, header at row 2) ──────────────────────
    print("\n--- ORDER SECTION (B3:D) ---")
    order_header = raw.iloc[2, 1:4].tolist()
    order_data   = raw.iloc[3:, 1:4].copy()
    order_data.columns = order_header
    order_data = order_data.dropna(how='all').reset_index(drop=True)
    print(f"Headers: {order_header}")
    print(order_data.to_string())

    # ── ORDER HISTORY SECTION: F3:I (cols 5-8, header at row 2) ─────────────
    print("\n--- ORDER HISTORY SECTION (F3:I) ---")
    hist_header = raw.iloc[2, 5:9].tolist()
    hist_data   = raw.iloc[3:, 5:9].copy()
    hist_data.columns = hist_header
    hist_data = hist_data.dropna(how='all').reset_index(drop=True)
    print(f"Headers: {hist_header}")
    print(hist_data.to_string())

    # ── BOM SECTION: K onwards (col 10+, may have multiple BOM blocks) ────────
    print("\n--- PRODUCTION BOM SECTION (col K onwards) ---")

    # Show raw BOM area first for visibility
    bom_area = raw.iloc[:, 10:].copy()
    print(f"\n[BOM RAW AREA - all rows, cols K onwards]:")
    print(bom_area.to_string())

    # Parse multiple BOM blocks
    # Structure per block: [empty row] → [title row] → [header row] → [data rows...]
    bom_blocks = []
    nrows = len(raw)
    col_start = 10  # Column K
    col_end   = 16  # Column P (+1)

    bom_section = raw.iloc[:, col_start:col_end].reset_index(drop=True)

    i = 0
    while i < len(bom_section):
        row = bom_section.iloc[i]
        non_null_vals = row.dropna()

        # Detect a "title" row: single non-null value (the BOM name)
        if len(non_null_vals) == 1 and i + 1 < len(bom_section):
            bom_title = str(non_null_vals.iloc[0])
            # Next row = header
            header_row = bom_section.iloc[i + 1]
            header_vals = header_row.dropna()
            if len(header_vals) >= 1:
                bom_headers = header_row.tolist()
                # Collect data rows until empty row
                data_rows = []
                j = i + 2
                while j < len(bom_section):
                    data_row = bom_section.iloc[j]
                    if data_row.dropna().shape[0] == 0:
                        break
                    data_rows.append(data_row.tolist())
                    j += 1
                if data_rows:
                    bom_df = pd.DataFrame(data_rows, columns=bom_headers)
                    bom_df.dropna(how='all', inplace=True)
                    bom_df.reset_index(drop=True, inplace=True)
                    bom_blocks.append((bom_title, bom_df))
                i = j + 1
            else:
                i += 1
        else:
            i += 1

    if bom_blocks:
        print(f"\n  Found {len(bom_blocks)} BOM block(s):\n")
        for title, bom_df in bom_blocks:
            print(f"  ── BOM: {title} ──")
            print(f"  Columns: {bom_df.columns.tolist()}")
            print(bom_df.to_string())
            print()
    else:
        print("\n  [NOTE] Auto-parser found no BOM blocks — review raw BOM area above.")

print("\n" + "=" * 70)
print("DATA LOADING COMPLETE")
print("=" * 70)
