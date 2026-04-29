"""
CMS Star Ratings Data Tables Processor + Snowflake Uploader
============================================================
Source : https://www.cms.gov/medicare/health-drug-plans/part-c-d-performance-data
Target : Latest Star Ratings Data Tables (ZIP)

USAGE — mix and match flags freely:

  # Download + process only
  py -3.11 process_star_ratings.py --auto
  py -3.11 process_star_ratings.py --zip ~/Downloads/2026-star-ratings-data-tables.zip
  py -3.11 process_star_ratings.py --url https://www.cms.gov/files/zip/2026-star-ratings-data-tables.zip

  # Download + process + upload to Snowflake
  py -3.11 process_star_ratings.py --auto --upload
  py -3.11 process_star_ratings.py --zip my.zip --upload

  # Upload only (skip download, use previously processed output)
  py -3.11 process_star_ratings.py --upload --no-process

SNOWFLAKE CONFIG — set via environment variables OR edit the SNOWFLAKE_CONFIG dict below:
  export SNOWFLAKE_ACCOUNT="myorg-myaccount"
  export SNOWFLAKE_USER="my_user"
  export SNOWFLAKE_PASSWORD="my_password"
  export SNOWFLAKE_WAREHOUSE="MY_WH"
  export SNOWFLAKE_DATABASE="MY_DB"
  export SNOWFLAKE_SCHEMA="MY_SCHEMA"
  export SNOWFLAKE_ROLE="MY_ROLE"          # optional

  Tables created:
    STAR_RATINGS_MEASURE_DATA       — measure performance values
    STAR_RATINGS_MEASURE_STARS      — measure star scores
    STAR_RATINGS_MEASURE_CROSSWALK  — measure name + reporting period lookup

OUTPUT (./star_ratings_output/):
  star_ratings_measure_data.xlsx
  star_ratings_measure_stars.xlsx
  star_ratings_measure_crosswalk_combined.xlsx

REQUIREMENTS:
  pip install pandas openpyxl requests selenium snowflake-connector-python
  Chrome + chromedriver (--auto mode only, auto-managed via webdriver-manager)
    pip install webdriver-manager    # handles Windows, macOS, and Linux automatically
    Or manually: https://chromedriver.chromium.org/downloads
"""

import os, sys, re, time, zipfile, argparse, tempfile, shutil, warnings
from pathlib import Path

import requests
import pandas as pd
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

warnings.filterwarnings("ignore")   # suppress Snowflake urllib3 noise

# ── SNOWFLAKE CONFIG ──────────────────────────────────────────────────────────
# Credentials are loaded automatically from config.json in the same folder.
# Run setup once:  py -3.11 process_star_ratings.py --setup
# You never need to edit this section manually.

def _load_config() -> dict:
    """Load Snowflake credentials from config.json, then env vars, then defaults."""
    import json
    cfg = {
        "account": "", "user": "", "password": "",
        "warehouse": "", "database": "", "schema": "", "role": ""
    }
    # 1. Load from config.json — check current dir first, then script dir
    for config_path in [Path.cwd() / "config.json", Path(__file__).parent / "config.json"]:
        if config_path.exists():
            try:
                with open(config_path) as f:
                    file_cfg = json.load(f)
                # Skip if values are still placeholders
                real = {k: v for k, v in file_cfg.items() 
                        if v and not str(v).startswith("your-") and v != ""}
                cfg.update(real)
                if real:
                    print(f"  Config loaded from: {config_path}")
                else:
                    print(f"  Warning: config.json found but still has placeholder values.")
                    print(f"  Please edit: {config_path}")
                break
            except Exception as e:
                print(f"  Warning: could not read config.json: {e}")
    # 2. Environment variables override config.json
    env_map = {
        "account": "SNOWFLAKE_ACCOUNT", "user": "SNOWFLAKE_USER",
        "password": "SNOWFLAKE_PASSWORD", "warehouse": "SNOWFLAKE_WAREHOUSE",
        "database": "SNOWFLAKE_DATABASE", "schema": "SNOWFLAKE_SCHEMA",
        "role": "SNOWFLAKE_ROLE",
    }
    for key, env in env_map.items():
        val = os.getenv(env, "")
        if val:
            cfg[key] = val
    return cfg

SNOWFLAKE_CONFIG = _load_config()

# Snowflake target table names
SF_TABLE_DATA      = "STAR_RATINGS_MEASURE_DATA"
SF_TABLE_STARS     = "STAR_RATINGS_MEASURE_STARS"
SF_TABLE_CROSSWALK = "STAR_RATINGS_MEASURE_CROSSWALK"

# ── CMS / FILE CONFIG ─────────────────────────────────────────────────────────
CMS_PAGE_URL       = "https://www.cms.gov/medicare/health-drug-plans/part-c-d-performance-data"
ZIP_LINK_KEYWORDS  = ["star ratings data tables", "star-ratings-data-tables"]
MEASURE_DATA_KEYWORDS  = ["measure data"]
MEASURE_STARS_KEYWORDS = ["measure stars", "measure star"]

# Additional tables available in the ZIP (loaded automatically)
SUMMARY_KEYWORDS       = ["summary ratings"]
DOMAIN_KEYWORDS        = ["domain stars"]
CUTPOINT_C_KEYWORDS    = ["part c cut points"]
CUTPOINT_D_KEYWORDS    = ["part d cut points"]
OUTPUT_DIR = Path(r"C:\Users\sadaf\OneDrive\Documents\Clean File for Opportunities\Local Copies")

# ── STYLES ────────────────────────────────────────────────────────────────────
_B = Side(style="thin", color="C8D6E8")
BORDER   = Border(left=_B, right=_B, top=_B, bottom=_B)
HDR_FILL = PatternFill("solid", fgColor="1F4E79")
ALT_FILL = PatternFill("solid", fgColor="EEF3F9")
WHT_FILL = PatternFill("solid", fgColor="FFFFFF")
HDR_FONT = Font(name="Arial", bold=True,  color="FFFFFF", size=10)
BOD_FONT = Font(name="Arial", bold=False, color="000000", size=9)
TTL_FONT = Font(name="Arial", bold=True,  color="1F4E79", size=11)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.cms.gov/",
}

SEP = "=" * 62


# ─────────────────────────────────────────────────────────────────────────────
#  DOWNLOAD LAYER
# ─────────────────────────────────────────────────────────────────────────────

def _find_zip_url_from_html(html: str, base_url: str) -> str:
    from urllib.parse import urljoin
    pattern = re.compile(r'href=["\']([^"\']+\.zip)["\']', re.IGNORECASE)
    for m in pattern.finditer(html):
        href  = m.group(1)
        lower = href.lower().replace("_", "-").replace(" ", "-")
        if any(kw.replace(" ", "-") in lower for kw in ZIP_LINK_KEYWORDS):
            return urljoin(base_url, href)
    for m in pattern.finditer(html):
        href = m.group(1).lower()
        if "star" in href and "rating" in href:
            from urllib.parse import urljoin as _uj
            return _uj(base_url, m.group(1))
    return None


def _stream_download(session: requests.Session, url: str, dest_dir: Path) -> Path:
    print(f"  Downloading: {url}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    filename = url.split("/")[-1].split("?")[0] or "star_ratings.zip"
    out_path = dest_dir / filename

    with session.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        total      = int(r.headers.get("content-length", 0))
        downloaded = 0
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    print(f"\r  Progress: {downloaded/total*100:.1f}%  ({downloaded/1024:.0f} KB)",
                          end="", flush=True)
    print()

    if out_path.stat().st_size < 1000:
        raise RuntimeError(f"Downloaded file too small ({out_path.stat().st_size} B) — likely an error page.")
    if not zipfile.is_zipfile(out_path):
        out_path.unlink()
        raise RuntimeError("Downloaded file is not a valid ZIP.")

    print(f"  Saved: {out_path.name}  ({out_path.stat().st_size/1024:.1f} KB)")
    return out_path


def download_via_requests(dest_dir: Path) -> Path:
    print("  [requests] Loading CMS page to establish session…")
    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)
    resp = session.get(CMS_PAGE_URL, timeout=30)
    resp.raise_for_status()

    zip_url = _find_zip_url_from_html(resp.text, CMS_PAGE_URL)
    if not zip_url:
        raise RuntimeError(
            "ZIP link not found in page HTML — page likely requires JavaScript.\n"
            "Use --auto which falls back to Selenium."
        )
    print(f"  [requests] ZIP URL: {zip_url}")
    return _stream_download(session, zip_url, dest_dir)


def download_via_selenium(dest_dir: Path) -> Path:
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.common.by import By
    except ImportError:
        raise RuntimeError("Run: pip install selenium")

    print("  [selenium] Launching headless Chrome…")
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1280,900")
    opts.add_argument(f"--user-agent={BROWSER_HEADERS['User-Agent']}")

    # Try webdriver-manager first (auto-downloads correct ChromeDriver for your Chrome version)
    # Works on Windows, macOS, and Linux with no manual install needed.
    # Install with: pip install webdriver-manager
    try:
        from webdriver_manager.chrome import ChromeDriverManager
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
        print("  [selenium] ChromeDriver auto-installed via webdriver-manager.")
    except ImportError:
        # webdriver-manager not installed, fall back to system ChromeDriver on PATH
        try:
            driver = webdriver.Chrome(options=opts)
        except Exception as e:
            raise RuntimeError(
                f"ChromeDriver not found: {e}\n\n"
                "Fix (easiest - works on Windows, Mac, Linux):\n"
                "  pip install webdriver-manager\n\n"
                "Or install ChromeDriver manually:\n"
                "  Windows : https://chromedriver.chromium.org/downloads\n"
                "            (place chromedriver.exe on your PATH)\n"
                "  macOS   : brew install chromedriver\n"
                "  Linux   : apt install chromium-driver"
            )
    except Exception as e:
        raise RuntimeError(f"ChromeDriver failed to start: {e}")

    zip_url = None
    try:
        driver.get(CMS_PAGE_URL)
        time.sleep(4)   # let JS render
        for link in driver.find_elements(By.TAG_NAME, "a"):
            href = (link.get_attribute("href") or "").lower()
            text = link.text.lower()
            if href.endswith(".zip") and (
                any(kw in href for kw in ["star-ratings-data-tables", "star_ratings_data_tables"])
                or "star ratings data tables" in text
            ):
                zip_url = link.get_attribute("href")
                print(f"  [selenium] Found: {zip_url}")
                break
        if not zip_url:
            # broader fallback
            for link in driver.find_elements(By.TAG_NAME, "a"):
                href = (link.get_attribute("href") or "").lower()
                if href.endswith(".zip") and "star" in href and "rating" in href:
                    zip_url = link.get_attribute("href")
                    break

        if not zip_url:
            raise RuntimeError(f"No ZIP link found on {CMS_PAGE_URL}")

        cookies = driver.get_cookies()
    finally:
        driver.quit()

    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)
    session.headers["Referer"] = CMS_PAGE_URL
    for c in cookies:
        session.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))
    return _stream_download(session, zip_url, dest_dir)


def auto_download(dest_dir: Path) -> Path:
    print("\n[AUTO-DOWNLOAD] Fetching from CMS…")
    try:
        return download_via_requests(dest_dir)
    except Exception as e:
        print(f"  requests failed: {e}")
        print("  Falling back to Selenium…")
        return download_via_selenium(dest_dir)


# ─────────────────────────────────────────────────────────────────────────────
#  ZIP / PARSE HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _norm(name: str) -> str:
    return re.sub(r"[_\-]+", " ", name.lower())


def _find_in_zip(zip_path: str, keywords: list) -> str:
    """Find a CSV or XLSX inside the ZIP whose name matches any keyword."""
    with zipfile.ZipFile(zip_path) as z:
        data_files = [n for n in z.namelist() 
                      if n.lower().endswith(".xlsx") or n.lower().endswith(".csv")]
    # Primary: keyword match on normalised filename
    for name in data_files:
        if any(kw in _norm(Path(name).name) for kw in keywords):
            return name
    # Fallback: keyword match anywhere in the full path
    for name in data_files:
        if any(kw in _norm(name) for kw in keywords):
            return name
    return None


def _list_xlsx(zip_path: str) -> list:
    """List all data files (CSV or XLSX) inside the ZIP."""
    with zipfile.ZipFile(zip_path) as z:
        return [n for n in z.namelist() 
                if n.lower().endswith(".xlsx") or n.lower().endswith(".csv")]


def _extract(zip_path: str, inner: str, dest: Path) -> Path:
    with zipfile.ZipFile(zip_path) as z:
        z.extract(inner, dest)
    p = dest / inner
    if not p.exists():
        hits = list(dest.rglob(Path(inner).name))
        p = hits[0] if hits else p
    return p


def _is_csv(path: Path) -> bool:
    return path.suffix.lower() == ".csv"


def _autowidth(ws, col_idx: int, max_w: int = 40):
    col  = get_column_letter(col_idx)
    best = max((len(str(ws.cell(row=r, column=col_idx).value or ""))
                for r in range(1, min(ws.max_row + 1, 300))), default=8)
    ws.column_dimensions[col].width = min(best + 2, max_w)


def parse_file(xlsx_path: Path):
    """
    CMS header split:
      Row 2, cols A-E  → fixed plan/contract headers
      Row 3, cols F+   → measure name headers
      Row 4            → reporting period (crosswalk only)
      Row 5+           → data
    Returns (data_df, crosswalk_df)
    """
    if _is_csv(xlsx_path):
        raw = pd.read_csv(xlsx_path, header=None, nrows=6, dtype=str, encoding="utf-8-sig").fillna("")
    else:
        raw = (pd.read_csv(xlsx_path, header=None, nrows=6, dtype=str, encoding="utf-8-sig") if _is_csv(xlsx_path) else pd.read_excel(xlsx_path, header=None, nrows=6, dtype=str)).fillna("")

    fixed = [str(v).strip() if str(v).strip() and str(v).lower() != "nan" else f"Field_{i+1}"
             for i, v in enumerate(raw.iloc[1, :5])]

    raw_meas   = [str(v).strip() for v in raw.iloc[2, 5:]]
    raw_period = [str(v).strip() for v in raw.iloc[3, 5:]]

    seen, meas_headers = {}, []
    for h in raw_meas:
        clean = h if h and h.lower() != "nan" else "Unknown"
        cnt   = seen.get(clean, 0)
        seen[clean] = cnt + 1
        meas_headers.append(clean if cnt == 0 else f"{clean}_{cnt}")

    all_headers = fixed + meas_headers
    print(f"    Fixed headers : {fixed}")
    print(f"    Measure cols  : {len(meas_headers)}")

    cw_rows = [{"Column_Index": i + 6,
                "Excel_Column": get_column_letter(i + 6),
                "Measure_Name": orig if orig and orig.lower() != "nan" else "Unknown",
                "Reporting_Period": p if p and p.lower() != "nan" else "Not Specified"}
               for i, (orig, p) in enumerate(zip(raw_meas, raw_period))]
    crosswalk_df = pd.DataFrame(cw_rows)

    data_df = (pd.read_csv(xlsx_path, header=None, skiprows=4, dtype=str, encoding="utf-8-sig") if _is_csv(xlsx_path) else pd.read_excel(xlsx_path, header=None, skiprows=4, dtype=str)).fillna("")
    if data_df.shape[1] > len(all_headers):
        data_df = data_df.iloc[:, :len(all_headers)]
    elif data_df.shape[1] < len(all_headers):
        all_headers = all_headers[:data_df.shape[1]]
    data_df.columns = all_headers
    data_df = data_df[~data_df.apply(lambda r: r.str.strip().eq("").all(), axis=1)].reset_index(drop=True)
    print(f"    Data rows     : {len(data_df)}")
    return data_df, crosswalk_df


# ─────────────────────────────────────────────────────────────────────────────
#  EXCEL WRITERS
# ─────────────────────────────────────────────────────────────────────────────

def _data_sheet(wb, df: pd.DataFrame, label: str):
    ws   = wb.active
    ws.title = "Data"
    span = min(len(df.columns), 8)
    ws.append([f"CMS Star Ratings - {label}"] + [""] * (span - 1))
    ws["A1"].font = TTL_FONT
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=span)
    ws.row_dimensions[1].height = 20

    ws.append(list(df.columns))
    hr = ws.max_row
    for cell in ws[hr]:
        cell.font = HDR_FONT; cell.fill = HDR_FILL
        cell.border = BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[hr].height = 24

    for i, row in enumerate(df.itertuples(index=False)):
        ws.append(list(row))
        rn   = ws.max_row
        fill = ALT_FILL if i % 2 == 1 else WHT_FILL
        for cell in ws[rn]:
            cell.font = BOD_FONT; cell.fill = fill
            cell.border = BORDER; cell.alignment = Alignment(vertical="center")
        ws.row_dimensions[rn].height = 15

    ws.freeze_panes = "F3"
    for ci in range(1, 6): _autowidth(ws, ci)
    for ci in range(6, len(df.columns) + 1):
        ws.column_dimensions[get_column_letter(ci)].width = 13


def _cw_sheet(wb, cw_df: pd.DataFrame, label: str):
    ws = wb.create_sheet("Measure Crosswalk")
    ws.append([f"Measure Crosswalk - {label}"] + ["", "", ""])
    ws["A1"].font = TTL_FONT
    ws.merge_cells("A1:D1")
    ws.row_dimensions[1].height = 20

    ws.append(list(cw_df.columns))
    hr = ws.max_row
    for cell in ws[hr]:
        cell.font = HDR_FONT; cell.fill = HDR_FILL
        cell.border = BORDER; cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[hr].height = 22

    for i, row in enumerate(cw_df.itertuples(index=False)):
        ws.append(list(row))
        rn = ws.max_row
        fill = ALT_FILL if i % 2 == 1 else WHT_FILL
        for cell in ws[rn]:
            cell.font = BOD_FONT; cell.fill = fill
            cell.border = BORDER; cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[rn].height = 30

    ws.freeze_panes = "A3"
    ws.column_dimensions["A"].width = 14; ws.column_dimensions["B"].width = 14
    ws.column_dimensions["C"].width = 60; ws.column_dimensions["D"].width = 32


def write_output(df: pd.DataFrame, cw_df: pd.DataFrame, out_path: Path, label: str):
    wb = openpyxl.Workbook()
    _data_sheet(wb, df, label)
    _cw_sheet(wb, cw_df, label)
    wb.save(out_path)
    print(f"  Saved: {out_path.name}  ({out_path.stat().st_size/1024:.1f} KB)")


def write_combined_crosswalk(cw_data: pd.DataFrame, cw_stars: pd.DataFrame, out_path: Path):
    wb = openpyxl.Workbook()
    ws = wb.active; ws.title = "Combined Crosswalk"
    ws.append(["CMS Star Ratings - Combined Measure Crosswalk"] + [""] * 5)
    ws["A1"].font = TTL_FONT; ws.merge_cells("A1:F1"); ws.row_dimensions[1].height = 20

    hdrs = ["Column_Index", "Excel_Column",
            "Measure_Name (Data File)", "Reporting_Period (Data File)",
            "Measure_Name (Stars File)", "Reporting_Period (Stars File)"]
    ws.append(hdrs)
    hr = ws.max_row
    for cell in ws[hr]:
        cell.font = HDR_FONT; cell.fill = HDR_FILL; cell.border = BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[hr].height = 30

    for i in range(max(len(cw_data), len(cw_stars))):
        d = cw_data.iloc[i].to_dict()  if i < len(cw_data)  else {}
        s = cw_stars.iloc[i].to_dict() if i < len(cw_stars) else {}
        ws.append([d.get("Column_Index",""), d.get("Excel_Column",""),
                   d.get("Measure_Name",""), d.get("Reporting_Period",""),
                   s.get("Measure_Name",""), s.get("Reporting_Period","")])
        rn = ws.max_row; fill = ALT_FILL if i % 2 == 1 else WHT_FILL
        for cell in ws[rn]:
            cell.font = BOD_FONT; cell.fill = fill; cell.border = BORDER
            cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[rn].height = 30

    ws.freeze_panes = "A3"
    for col, w in zip("ABCDEF", [14, 14, 55, 32, 55, 32]):
        ws.column_dimensions[col].width = w
    wb.save(out_path)
    print(f"  Saved: {out_path.name}  ({out_path.stat().st_size/1024:.1f} KB)")


# ─────────────────────────────────────────────────────────────────────────────
#  SNOWFLAKE UPLOAD LAYER
# ─────────────────────────────────────────────────────────────────────────────

def _sf_safe_col(name: str) -> str:
    """Convert a column name to a Snowflake-safe identifier."""
    s = re.sub(r"[^A-Za-z0-9_]", "_", str(name).strip())
    s = re.sub(r"_+", "_", s).strip("_")
    if s and s[0].isdigit():
        s = "COL_" + s
    return s.upper() or "COL"


def _df_to_snowflake_ddl(df: pd.DataFrame, table_name: str, db: str, schema: str) -> str:
    """Generate a CREATE OR REPLACE TABLE DDL from a DataFrame."""
    cols = []
    for col in df.columns:
        sf_col = _sf_safe_col(col)
        cols.append(f'    "{sf_col}" VARCHAR')
    col_defs = ",\n".join(cols)
    return f'CREATE OR REPLACE TABLE "{db}"."{schema}"."{table_name}" (\n{col_defs}\n);'


def _validate_sf_config(cfg: dict):
    """Raise if any required Snowflake config key is missing."""
    required = ["account", "user", "password", "warehouse", "database", "schema"]
    missing  = [k for k in required if not cfg.get(k)]
    if missing:
        raise ValueError(
            f"Missing Snowflake config: {missing}\n"
            "Set environment variables or edit SNOWFLAKE_CONFIG in the script.\n"
            "  export SNOWFLAKE_ACCOUNT='myorg-myaccount'\n"
            "  export SNOWFLAKE_USER='my_user'\n"
            "  export SNOWFLAKE_PASSWORD='my_password'\n"
            "  export SNOWFLAKE_WAREHOUSE='MY_WH'\n"
            "  export SNOWFLAKE_DATABASE='MY_DB'\n"
            "  export SNOWFLAKE_SCHEMA='MY_SCHEMA'"
        )


def upload_df_to_snowflake(
    df: pd.DataFrame,
    table_name: str,
    con,
    db: str,
    schema: str,
):
    """
    Upload a DataFrame to Snowflake using write_pandas (fastest path).
    Falls back to chunked INSERT if write_pandas isn't available.
    Table is created or replaced automatically.
    """
    import snowflake.connector
    from snowflake.connector.pandas_tools import write_pandas

    # Rename columns to Snowflake-safe names
    safe_cols = {col: _sf_safe_col(col) for col in df.columns}
    df_upload = df.rename(columns=safe_cols).copy()

    # Create table
    ddl = _df_to_snowflake_ddl(df, table_name, db, schema)
    cur = con.cursor()
    try:
        cur.execute(f'USE DATABASE "{db}"')
        cur.execute(f'USE SCHEMA "{schema}"')
        cur.execute(ddl)
        print(f"    Table created/replaced: {db}.{schema}.{table_name}")

        # Upload via write_pandas (uses PUT + COPY INTO — very fast)
        success, nchunks, nrows, _ = write_pandas(
            conn=con,
            df=df_upload,
            table_name=table_name,
            database=db,
            schema=schema,
            overwrite=True,
            quote_identifiers=True,
            auto_create_table=False,
        )
        if success:
            print(f"    Uploaded {nrows:,} rows in {nchunks} chunk(s)")
        else:
            raise RuntimeError("write_pandas returned failure status")
    finally:
        cur.close()


def snowflake_upload(results: dict, crosswalks: dict):
    """Upload all three datasets to Snowflake."""
    print(f"\n{SEP}")
    print("  SNOWFLAKE UPLOAD")
    print(SEP)

    _validate_sf_config(SNOWFLAKE_CONFIG)

    try:
        import snowflake.connector
    except ImportError:
        raise RuntimeError("Run: pip install snowflake-connector-python[pandas]")

    cfg = SNOWFLAKE_CONFIG
    db     = cfg["database"].upper()
    schema = cfg["schema"].upper()

    # Build connection kwargs
    conn_kwargs = {
        "account"  : cfg["account"],
        "user"     : cfg["user"],
        "password" : cfg["password"],
        "warehouse": cfg["warehouse"],
        "database" : db,
        "schema"   : schema,
        "session_parameters": {"QUERY_TAG": "CMS_Star_Ratings_Processor"},
    }
    if cfg.get("role"):
        conn_kwargs["role"] = cfg["role"]

    print(f"  Connecting to Snowflake…")
    print(f"    Account  : {cfg['account']}")
    print(f"    Database : {db}")
    print(f"    Schema   : {schema}")
    print(f"    Warehouse: {cfg['warehouse']}")
    if cfg.get("role"):
        print(f"    Role     : {cfg['role']}")

    con = snowflake.connector.connect(**conn_kwargs)
    try:
        cur = con.cursor()
        cur.execute(f'USE WAREHOUSE "{cfg["warehouse"]}"')
        cur.close()
        print("  Connection successful.\n")

        # Upload Measure Data
        if "Measure Data" in results:
            print(f"  Uploading {SF_TABLE_DATA}…")
            upload_df_to_snowflake(results["Measure Data"], SF_TABLE_DATA, con, db, schema)

        # Upload Measure Stars
        if "Measure Stars" in results:
            print(f"\n  Uploading {SF_TABLE_STARS}…")
            upload_df_to_snowflake(results["Measure Stars"], SF_TABLE_STARS, con, db, schema)

        # Upload combined crosswalk
        if len(crosswalks) == 2:
            print(f"\n  Uploading {SF_TABLE_CROSSWALK}…")
            # Build combined crosswalk DataFrame
            cw_data  = crosswalks["Measure Data"]
            cw_stars = crosswalks["Measure Stars"]
            n = max(len(cw_data), len(cw_stars))
            cw_combined = pd.DataFrame([{
                "Column_Index"              : cw_data.iloc[i]["Column_Index"]   if i < len(cw_data)  else "",
                "Excel_Column"              : cw_data.iloc[i]["Excel_Column"]   if i < len(cw_data)  else "",
                "Measure_Name_Data"         : cw_data.iloc[i]["Measure_Name"]   if i < len(cw_data)  else "",
                "Reporting_Period_Data"     : cw_data.iloc[i]["Reporting_Period"] if i < len(cw_data) else "",
                "Measure_Name_Stars"        : cw_stars.iloc[i]["Measure_Name"]  if i < len(cw_stars) else "",
                "Reporting_Period_Stars"    : cw_stars.iloc[i]["Reporting_Period"] if i < len(cw_stars) else "",
            } for i in range(n)])
            upload_df_to_snowflake(cw_combined, SF_TABLE_CROSSWALK, con, db, schema)

        elif "Measure Data" in crosswalks:
            print(f"\n  Uploading {SF_TABLE_CROSSWALK} (Data only)…")
            upload_df_to_snowflake(crosswalks["Measure Data"], SF_TABLE_CROSSWALK, con, db, schema)

        print(f"\n  All tables loaded successfully into {db}.{schema}")

    finally:
        con.close()

    # Print quick-start queries
    print(f"""
  Quick-start queries:
  ───────────────────────────────────────────────────────────
  -- Preview data
  SELECT * FROM "{db}"."{schema}"."{SF_TABLE_DATA}" LIMIT 10;
  SELECT * FROM "{db}"."{schema}"."{SF_TABLE_STARS}" LIMIT 10;

  -- Crosswalk lookup
  SELECT * FROM "{db}"."{schema}"."{SF_TABLE_CROSSWALK}";

  -- Join data + crosswalk
  SELECT d.*, c."MEASURE_NAME_DATA", c."REPORTING_PERIOD_DATA"
  FROM   "{db}"."{schema}"."{SF_TABLE_DATA}" d
  JOIN   "{db}"."{schema}"."{SF_TABLE_CROSSWALK}" c
    ON   d."CONTRACT_ID" IS NOT NULL   -- replace with real join key
  LIMIT 20;
  ───────────────────────────────────────────────────────────
""")


# ─────────────────────────────────────────────────────────────────────────────
#  PROCESS ZIP
# ─────────────────────────────────────────────────────────────────────────────

def process_zip(zip_path: Path):
    OUTPUT_DIR.mkdir(exist_ok=True)
    # Use system temp dir for extraction to avoid OneDrive/permission issues
    import tempfile as _tempfile
    _tmp_holder = _tempfile.TemporaryDirectory(prefix="cms_stars_extract_")
    extract_dir = Path(_tmp_holder.name)

    print(f"\n{SEP}")
    print("  CMS Star Ratings Data Tables Processor")
    print(SEP)
    print(f"  ZIP   : {zip_path}")
    print(f"  Output: {OUTPUT_DIR.resolve()}\n")

    print("[1/5] Scanning ZIP…")
    all_xlsx = _list_xlsx(str(zip_path))
    print(f"  Found {len(all_xlsx)} .xlsx file(s):")
    for x in all_xlsx: print(f"    {x}")

    data_inner  = _find_in_zip(str(zip_path), MEASURE_DATA_KEYWORDS)
    stars_inner = _find_in_zip(str(zip_path), MEASURE_STARS_KEYWORDS)
    if not data_inner:  print(f"\n  WARNING: Measure Data not found  (keywords: {MEASURE_DATA_KEYWORDS})")
    if not stars_inner: print(f"\n  WARNING: Measure Stars not found (keywords: {MEASURE_STARS_KEYWORDS})")
    if not data_inner and not stars_inner:
        print("\nERROR: No files matched. Edit keyword constants at top of script.")
        sys.exit(1)
    print(f"\n  Measure Data  -> {data_inner  or 'NOT FOUND'}")
    print(f"  Measure Stars -> {stars_inner or 'NOT FOUND'}")

    print("\n[2/5] Extracting…")
    extracted = {}
    for label, inner in [("Measure Data", data_inner), ("Measure Stars", stars_inner)]:
        if inner:
            p = _extract(str(zip_path), inner, extract_dir)
            extracted[label] = p
            print(f"  {label}: {p.name}")

    print("\n[3/5] Parsing…")
    results, crosswalks = {}, {}
    for label, path in extracted.items():
        print(f"  {label}:")
        df, cw = parse_file(path)
        results[label] = df; crosswalks[label] = cw

    print("\n[4/5] Writing Excel outputs…")
    if "Measure Data" in results:
        write_output(results["Measure Data"], crosswalks["Measure Data"],
                     OUTPUT_DIR / "star_ratings_measure_data.xlsx",
                     "Star Ratings Data Table - Measure Data")
    if "Measure Stars" in results:
        write_output(results["Measure Stars"], crosswalks["Measure Stars"],
                     OUTPUT_DIR / "star_ratings_measure_stars.xlsx",
                     "Star Ratings Data Table - Measure Stars")
    if len(crosswalks) == 2:
        write_combined_crosswalk(crosswalks["Measure Data"], crosswalks["Measure Stars"],
                                 OUTPUT_DIR / "star_ratings_measure_crosswalk_combined.xlsx")

    print(f"\n[5/5] Excel output complete.")
    print(f"\n  Files in {OUTPUT_DIR.resolve()}:")
    for f in sorted(OUTPUT_DIR.glob("*.xlsx")):
        print(f"    {f.name}  ({f.stat().st_size/1024:.1f} KB)")

    return results, crosswalks


def load_existing_outputs() -> tuple:
    """Load previously processed Excel outputs from disk (for --upload --no-process)."""
    results, crosswalks = {}, {}
    data_path  = OUTPUT_DIR / "star_ratings_measure_data.xlsx"
    stars_path = OUTPUT_DIR / "star_ratings_measure_stars.xlsx"

    if data_path.exists():
        print(f"  Loading existing: {data_path.name}")
        df = pd.read_excel(data_path, sheet_name="Data", header=1, dtype=str).fillna("")
        # Drop the merged title row if it snuck in
        df = df[~df.apply(lambda r: r.str.strip().eq("").all(), axis=1)].iloc[1:].reset_index(drop=True)
        # Also reload crosswalk
        cw = pd.read_excel(data_path, sheet_name="Measure Crosswalk", header=1, dtype=str).fillna("")
        cw = cw[~cw.apply(lambda r: r.str.strip().eq("").all(), axis=1)].iloc[1:].reset_index(drop=True)
        results["Measure Data"]    = df
        crosswalks["Measure Data"] = cw

    if stars_path.exists():
        print(f"  Loading existing: {stars_path.name}")
        df = pd.read_excel(stars_path, sheet_name="Data", header=1, dtype=str).fillna("")
        df = df[~df.apply(lambda r: r.str.strip().eq("").all(), axis=1)].iloc[1:].reset_index(drop=True)
        cw = pd.read_excel(stars_path, sheet_name="Measure Crosswalk", header=1, dtype=str).fillna("")
        cw = cw[~cw.apply(lambda r: r.str.strip().eq("").all(), axis=1)].iloc[1:].reset_index(drop=True)
        results["Measure Stars"]    = df
        crosswalks["Measure Stars"] = cw

    if not results:
        raise FileNotFoundError(
            f"No processed output files found in {OUTPUT_DIR}.\n"
            "Run without --no-process first to generate them."
        )
    return results, crosswalks


# ─────────────────────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
#  MA CONTRACT DIRECTORY UPLOAD
# ─────────────────────────────────────────────────────────────────────────────

def process_ma_directory(xlsx_path: Path, con, db: str, schema: str):
    """
    Read MA_Contract_directory_2026_04.xlsx:
      - Row 1 = headers (spaces replaced with underscores, uppercased)
      - Upload to Snowflake table MA_CONTRACT_DIRECTORY_2026_04
    """
    from snowflake.connector.pandas_tools import write_pandas

    print(f"\n  Reading: {xlsx_path.name}")
    df = pd.read_excel(xlsx_path, header=0, dtype=str).fillna("")

    # Clean column names: spaces -> underscores, uppercase
    df.columns = [re.sub(r"\s+", "_", c.strip()).upper() for c in df.columns]
    # Also remove any special characters
    df.columns = [re.sub(r"[^A-Z0-9_]", "", c) for c in df.columns]

    print(f"  Columns  : {list(df.columns)}")
    print(f"  Rows     : {len(df)}")

    table_name = "MA_CONTRACT_DIRECTORY_2026_04"

    # Build DDL
    col_defs = ",\n".join([f'    "{c}" VARCHAR' for c in df.columns])
    ddl = f'CREATE OR REPLACE TABLE "{db}"."{schema}"."{table_name}" (\n{col_defs}\n);' 

    cur = con.cursor()
    try:
        cur.execute(f'USE DATABASE "{db}"')
        cur.execute(f'USE SCHEMA "{schema}"')
        cur.execute(ddl)
        print(f"  Table created: {db}.{schema}.{table_name}")

        success, nchunks, nrows, _ = write_pandas(
            conn=con, df=df, table_name=table_name,
            database=db, schema=schema,
            overwrite=True, quote_identifiers=True,
            auto_create_table=False,
        )
        if success:
            print(f"  Uploaded {nrows:,} rows in {nchunks} chunk(s)")
        else:
            raise RuntimeError("write_pandas failed")
    finally:
        cur.close()



# ─────────────────────────────────────────────────────────────────────────────
#  PART C / PART D CUT POINTS UPLOAD
# ─────────────────────────────────────────────────────────────────────────────

def process_cut_points(zip_path: Path, con, db: str, schema: str):
    """
    Read Part C and Part D Cut Points CSVs from ZIP.

    Structure:
      Row 1: Table title (skip)
      Row 2: Domain headers (skip)
      Row 3: A3="Measure_Name" + measure name headers for cols B+
      Row 4: A4="Reporting_Period" + reporting period per measure (crosswalk)
      Row 5+: Data rows (1star, 2star, 3star, 4star, 5star)
    """
    import io
    from snowflake.connector.pandas_tools import write_pandas

    targets = [
        (CUTPOINT_C_KEYWORDS, "STAR_RATINGS_PART_C_CUT_POINTS"),
        (CUTPOINT_D_KEYWORDS, "STAR_RATINGS_PART_D_CUT_POINTS"),
    ]

    for keywords, table_name in targets:
        inner = _find_in_zip(str(zip_path), keywords)
        if not inner:
            print(f"  WARNING: Could not find file for {table_name}")
            continue

        print(f"\n  Processing: {inner} -> {table_name}")

        with zipfile.ZipFile(str(zip_path)) as z:
            raw_bytes = z.read(inner)

        # Read first 6 rows raw to inspect structure
        raw = pd.read_csv(
            io.BytesIO(raw_bytes), header=None, nrows=6,
            dtype=str, encoding="utf-8-sig"
        ).fillna("")

        # Row 3 (index 2) = headers
        # A3 = "Measure_Name", B3+ = measure names
        headers = []
        for i, v in enumerate(raw.iloc[2]):
            s = str(v).strip()
            if i == 0:
                headers.append("Measure_Name")
            else:
                headers.append(s if s and s.lower() != "nan" else f"Measure_{i}")

        # Row 4 (index 3) = reporting periods per measure (for crosswalk)
        reporting = []
        for i, v in enumerate(raw.iloc[3]):
            s = str(v).strip()
            if i == 0:
                reporting.append("Reporting_Period")
            else:
                reporting.append(s if s and s.lower() != "nan" else "")

        print(f"  Headers sample : {headers[:5]}")
        print(f"  Reporting sample: {reporting[:5]}")

        # Read data rows — skip first 4 rows (title, domain, header, reporting period)
        data_df = pd.read_csv(
            io.BytesIO(raw_bytes), header=None, skiprows=4,
            dtype=str, encoding="utf-8-sig"
        ).fillna("")

        # Trim/pad columns to match headers
        if data_df.shape[1] > len(headers):
            data_df = data_df.iloc[:, :len(headers)]
        elif data_df.shape[1] < len(headers):
            headers = headers[:data_df.shape[1]]

        data_df.columns = headers

        # Drop fully blank rows
        data_df = data_df[
            ~data_df.apply(lambda r: r.str.strip().eq("").all(), axis=1)
        ].reset_index(drop=True)

        print(f"  Data rows : {len(data_df)}")

        # Upload to Snowflake
        safe_cols = {col: _sf_safe_col(col) for col in data_df.columns}
        df_upload = data_df.rename(columns=safe_cols)

        col_defs = ",\n".join([f'    "{c}" VARCHAR' for c in df_upload.columns])
        ddl = f'CREATE OR REPLACE TABLE "{db}"."{schema}"."{table_name}" (\n{col_defs}\n);' 

        cur = con.cursor()
        try:
            cur.execute(f'USE DATABASE "{db}"')
            cur.execute(f'USE SCHEMA "{schema}"')
            cur.execute(ddl)
            print(f"  Table created: {db}.{schema}.{table_name}")

            success, nchunks, nrows, _ = write_pandas(
                conn=con, df=df_upload, table_name=table_name,
                database=db, schema=schema,
                overwrite=True, quote_identifiers=True,
                auto_create_table=False,
            )
            if success:
                print(f"  Uploaded {nrows:,} rows in {nchunks} chunk(s)")
            else:
                raise RuntimeError("write_pandas failed")
        finally:
            cur.close()



# ─────────────────────────────────────────────────────────────────────────────
#  AD-HOC CAP SUMMARY REPORT UPLOAD
# ─────────────────────────────────────────────────────────────────────────────

def process_cap_summary(html_path: Path, con, db: str, schema: str):
    """
    Parse Ad-Hoc CAP Summary Report HTML:
      - Splits Contract ID from Contract Name into separate columns
      - Expands multiple contracts per row into individual rows
      - Replaces spaces with underscores in all column headers
      - Uploads to ADHOC_CAP_SUMMARY table in Snowflake
    """
    from bs4 import BeautifulSoup
    from snowflake.connector.pandas_tools import write_pandas
    import pandas as pd

    print(f"\n  Reading: {html_path.name}")

    with open(html_path, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "html.parser")

    rows = soup.find_all("tr", align="left")
    records = []

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 10:
            continue

        cell_text = cells[0].get_text(separator="\n").strip()
        contract_entries = [line.strip() for line in cell_text.split("\n") if line.strip()]

        parsed_contracts = []
        for entry in contract_entries:
            if " - " in entry:
                parts = entry.split(" - ", 1)
                parsed_contracts.append((parts[0].strip(), parts[1].strip()))
            elif entry:
                parsed_contracts.append((entry.strip(), ""))

        parent_org    = cells[1].get_text(strip=True)
        contact_name  = cells[2].get_text(strip=True)
        contact_phone = cells[3].get_text(strip=True)
        issue_id      = cells[4].get_text(strip=True)
        date_sent     = cells[5].get_text(strip=True)
        issue_type    = cells[6].get_text(strip=True)
        issue_topic   = cells[7].get_text(strip=True)
        issue_summary = cells[8].get_text(strip=True)
        letter_name   = cells[9].get_text(strip=True)

        for contract_id, contract_name in parsed_contracts:
            records.append([
                contract_id, contract_name,
                parent_org, contact_name, contact_phone,
                issue_id, date_sent, issue_type,
                issue_topic, issue_summary, letter_name
            ])

    headers = [
        "Contract_ID", "Contract_Name",
        "Parent_Organization_Name", "Organization_Contact_Name",
        "Organization_Contact_Phone", "Compliance_Issue_ID",
        "Date_Letter_Sent", "Issue_Type",
        "Issue_Topic", "Issue_Summary", "Letter_Name"
    ]

    df = pd.DataFrame(records, columns=headers)
    print(f"  Records  : {len(df)}")
    print(f"  Columns  : {list(df.columns)}")

    table_name = "ADHOC_CAP_SUMMARY"
    col_defs = ",\n".join([f'    "{c}" VARCHAR' for c in df.columns])
    ddl = f'CREATE OR REPLACE TABLE "{db}"."{schema}"."{table_name}" (\n{col_defs}\n);'

    cur = con.cursor()
    try:
        cur.execute(f'USE DATABASE "{db}"')
        cur.execute(f'USE SCHEMA "{schema}"')
        cur.execute(ddl)
        print(f"  Table created: {db}.{schema}.{table_name}")

        success, nchunks, nrows, _ = write_pandas(
            conn=con, df=df, table_name=table_name,
            database=db, schema=schema,
            overwrite=True, quote_identifiers=True,
            auto_create_table=False,
        )
        if success:
            print(f"  Uploaded {nrows:,} rows in {nchunks} chunk(s)")
        else:
            raise RuntimeError("write_pandas failed")
    finally:
        cur.close()


# ─────────────────────────────────────────────────────────────────────────────
#  GENERIC CMS CSV TABLE UPLOAD (Row 1 = skip, Row 2 = headers)
# ─────────────────────────────────────────────────────────────────────────────

# Table configs: (keyword, snowflake_table_name)
EXTRA_TABLES = [
    (["cai"],                         "STAR_RATINGS_CAI"),
    (["domain stars", "domain star"], "STAR_RATINGS_DOMAIN_STARS"),
    (["summary ratings"],             "STAR_RATINGS_SUMMARY_RATINGS"),
    (["high performing"],             "STAR_RATINGS_HIGH_PERFORMING_CONTRACTS"),
    (["low performing"],              "STAR_RATINGS_LOW_PERFORMING_CONTRACTS"),
]


def upload_generic_csv(zip_path: Path, keywords: list, table_name: str,
                       con, db: str, schema: str):
    """
    Upload a CMS CSV file from ZIP where:
      Row 1 = table title (skip)
      Row 2 = headers (spaces -> underscores, trimmed, uppercased)
      Row 3+ = data
    """
    import io
    from snowflake.connector.pandas_tools import write_pandas

    inner = _find_in_zip(str(zip_path), keywords)
    if not inner:
        print(f"  WARNING: Could not find file for {table_name} (keywords: {keywords})")
        return

    print(f"\n  Processing: {Path(inner).name}")
    print(f"  Target table: {table_name}")

    with zipfile.ZipFile(str(zip_path)) as z:
        raw_bytes = z.read(inner)

    # Read row 2 (index 1) as headers
    header_row = pd.read_csv(
        io.BytesIO(raw_bytes), header=None, skiprows=1, nrows=1,
        dtype=str, encoding="utf-8-sig"
    ).fillna("").iloc[0].tolist()

    # Clean headers: trim, spaces -> underscores, uppercase, remove special chars
    clean_headers = []
    seen = {}
    for h in header_row:
        h = str(h).strip()
        h = re.sub(r"\s+", "_", h)
        h = re.sub(r"[^A-Za-z0-9_]", "", h).upper()
        h = h if h else "COL"
        cnt = seen.get(h, 0)
        seen[h] = cnt + 1
        clean_headers.append(h if cnt == 0 else f"{h}_{cnt}")

    print(f"  Headers ({len(clean_headers)}): {clean_headers[:6]}...")

    # Read data rows (skip row 1 title + row 2 headers = skip 2 rows)
    data_df = pd.read_csv(
        io.BytesIO(raw_bytes), header=None, skiprows=2,
        dtype=str, encoding="utf-8-sig"
    ).fillna("")

    # Trim/pad columns
    if data_df.shape[1] > len(clean_headers):
        data_df = data_df.iloc[:, :len(clean_headers)]
    elif data_df.shape[1] < len(clean_headers):
        clean_headers = clean_headers[:data_df.shape[1]]

    data_df.columns = clean_headers

    # Drop fully blank rows
    data_df = data_df[
        ~data_df.apply(lambda r: r.str.strip().eq("").all(), axis=1)
    ].reset_index(drop=True)

    print(f"  Rows: {len(data_df)}")

    # Upload to Snowflake
    col_defs = ",\n".join([f'    "{c}" VARCHAR' for c in data_df.columns])
    ddl = f'CREATE OR REPLACE TABLE "{db}"."{schema}"."{table_name}" (\n{col_defs}\n);'

    cur = con.cursor()
    try:
        cur.execute(f'USE DATABASE "{db}"')
        cur.execute(f'USE SCHEMA "{schema}"')
        cur.execute(ddl)
        print(f"  Table created: {db}.{schema}.{table_name}")

        success, nchunks, nrows, _ = write_pandas(
            conn=con, df=data_df, table_name=table_name,
            database=db, schema=schema,
            overwrite=True, quote_identifiers=True,
            auto_create_table=False,
        )
        if success:
            print(f"  Uploaded {nrows:,} rows in {nchunks} chunk(s)")
        else:
            raise RuntimeError("write_pandas failed")
    finally:
        cur.close()


def process_extra_tables(zip_path: Path, con, db: str, schema: str):
    """Upload all 5 extra CMS tables: CAI, Domain Stars, Summary Ratings,
    High Performing, Low Performing Contracts."""
    for keywords, table_name in EXTRA_TABLES:
        upload_generic_csv(zip_path, keywords, table_name, con, db, schema)


# ─────────────────────────────────────────────────────────────────────────────
#  2027 STAR RATINGS MEASURES UPLOAD (Table 1 Part C + Table 2 Part D)
# ─────────────────────────────────────────────────────────────────────────────

def process_star_measures(zip_path: Path, con, db: str, schema: str):
    """
    Upload 2027 Star Ratings measure tables from ZIP:
      - 2027_Star_Ratings_Table1_Part_C.xlsx  -> STAR_RATINGS_2027_PART_C_MEASURES
      - 2027_Star_Ratings_Table2_Part_D.xlsx  -> STAR_RATINGS_2027_PART_D_MEASURES
    Row 1 = title (skip), Row 2 = headers
    Headers: spaces -> underscores, trimmed, uppercased
    Weight column stored as number
    """
    import io
    from snowflake.connector.pandas_tools import write_pandas

    targets = [
        (["table1", "part_c", "part c"], "STAR_RATINGS_2027_PART_C_MEASURES"),
        (["table2", "part_d", "part d"], "STAR_RATINGS_2027_PART_D_MEASURES"),
    ]

    with zipfile.ZipFile(str(zip_path)) as z:
        all_files = z.namelist()

    for keywords, table_name in targets:
        # Find matching file
        inner = None
        for fname in all_files:
            norm = _norm(Path(fname).name)
            if any(kw in norm for kw in keywords):
                inner = fname
                break

        if not inner:
            print(f"  WARNING: Could not find file for {table_name}")
            print(f"  Files in ZIP: {all_files}")
            continue

        print(f"\n  Processing: {Path(inner).name}")
        print(f"  Target table: {table_name}")

        with zipfile.ZipFile(str(zip_path)) as z:
            raw_bytes = z.read(inner)

        # Row 2 (index 1) = headers, skip row 1 (title)
        df = pd.read_excel(
            io.BytesIO(raw_bytes), header=1, dtype=str
        ).fillna("")

        # Clean headers: trim, spaces -> underscores, uppercase
        df.columns = [
            re.sub(r"[^A-Z0-9_]", "", re.sub(r"\s+", "_", str(c).strip())).upper()
            for c in df.columns
        ]

        # Trim all string fields
        for col in df.columns:
            df[col] = df[col].str.strip()

        # Convert weight column to numeric
        weight_cols = [c for c in df.columns if "WEIGHT" in c]
        for wc in weight_cols:
            df[wc] = pd.to_numeric(df[wc], errors="coerce").fillna(0).astype(int).astype(str)

        # Drop fully blank rows
        df = df[~df.apply(lambda r: r.str.strip().eq("").all(), axis=1)].reset_index(drop=True)

        print(f"  Columns : {list(df.columns)}")
        print(f"  Rows    : {len(df)}")

        # Upload
        col_defs = ",\n".join([f'    "{c}" VARCHAR' for c in df.columns])
        ddl = f'CREATE OR REPLACE TABLE "{db}"."{schema}"."{table_name}" (\n{col_defs}\n);'

        cur = con.cursor()
        try:
            cur.execute(f'USE DATABASE "{db}"')
            cur.execute(f'USE SCHEMA "{schema}"')
            cur.execute(ddl)
            print(f"  Table created: {db}.{schema}.{table_name}")

            success, nchunks, nrows, _ = write_pandas(
                conn=con, df=df, table_name=table_name,
                database=db, schema=schema,
                overwrite=True, quote_identifiers=True,
                auto_create_table=False,
            )
            if success:
                print(f"  Uploaded {nrows:,} rows in {nchunks} chunk(s)")
            else:
                raise RuntimeError("write_pandas failed")
        finally:
            cur.close()


# ─────────────────────────────────────────────────────────────────────────────
#  CAP INFO TABLE UPLOAD (from Excel file)
# ─────────────────────────────────────────────────────────────────────────────

def process_cap_info(xlsx_path: Path, con, db: str, schema: str):
    """
    Upload CAP letter contact info from Excel to Snowflake CAP_INFO table.
    Maps: Source File -> FILE_NAME, Recipient Name -> RECIPIENT_NAME,
          Email Address -> EMAIL, Date of Letter -> DATE_OF_LETTER,
          Contract ID -> CONTRACT_ID, Issue Type -> SUMMARY
    """
    import io
    from snowflake.connector.pandas_tools import write_pandas
    import pandas as pd

    print(f"\n  Reading: {xlsx_path.name}")

    df = pd.read_excel(str(xlsx_path), header=1, dtype=str).fillna("")

    # Map columns to Snowflake field names
    df = df.rename(columns={
        "Source File":       "FILE_NAME",
        "Recipient Name":    "RECIPIENT_NAME",
        "Email Address":     "EMAIL",
        "Date of Letter":    "DATE_OF_LETTER",
        "Contract ID":       "CONTRACT_ID",
        "Issue Type":        "SUMMARY",
    })

    # Keep only target columns
    keep = ["FILE_NAME","RECIPIENT_NAME","EMAIL","DATE_OF_LETTER","CONTRACT_ID","SUMMARY"]
    df = df[[c for c in keep if c in df.columns]]

    # Clean — trim all fields
    for col in df.columns:
        df[col] = df[col].str.strip()

    # Drop blank rows
    df = df[df["CONTRACT_ID"].str.strip().ne("")].reset_index(drop=True)

    print(f"  Records : {len(df)}")
    print(f"  Columns : {list(df.columns)}")

    table_name = "CAP_INFO"
    col_defs = ",\n".join([
        f'    "FILE_NAME"      VARCHAR',
        f'    "RECIPIENT_NAME" VARCHAR',
        f'    "EMAIL"          VARCHAR',
        f'    "DATE_OF_LETTER" VARCHAR',
        f'    "CONTRACT_ID"    VARCHAR',
        f'    "SUMMARY"        VARCHAR',
    ])
    ddl = f'CREATE OR REPLACE TABLE "{db}"."{schema}"."{table_name}" (\n{col_defs}\n);'

    cur = con.cursor()
    try:
        cur.execute(f'USE DATABASE "{db}"')
        cur.execute(f'USE SCHEMA "{schema}"')
        cur.execute(ddl)
        print(f"  Table created: {db}.{schema}.{table_name}")

        success, nchunks, nrows, _ = write_pandas(
            conn=con, df=df, table_name=table_name,
            database=db, schema=schema,
            overwrite=True, quote_identifiers=True,
            auto_create_table=False,
        )
        if success:
            print(f"  Uploaded {nrows:,} rows in {nchunks} chunk(s)")
        else:
            raise RuntimeError("write_pandas failed")
    finally:
        cur.close()

def run_setup():
    """Interactive wizard to save Snowflake credentials to config.json."""
    import json, getpass
    config_path = Path(__file__).parent / "config.json"

    print()
    print("=" * 62)
    print("  Snowflake Credentials Setup")
    print("=" * 62)
    print(f"  Credentials will be saved to: {config_path}")
    print("  Press Enter to keep existing value shown in [ ].")
    print()

    # Load existing config if present
    existing = {}
    if config_path.exists():
        try:
            with open(config_path) as f:
                existing = json.load(f)
        except Exception:
            pass

    def _prompt(label, key, secret=False):
        current = existing.get(key, "")
        display = ("*" * min(len(current), 8)) if (secret and current) else current
        prompt_str = f"  {label} [{display}]: "
        val = getpass.getpass(prompt_str) if secret else input(prompt_str)
        return val.strip() if val.strip() else current

    cfg = {
        "account"  : _prompt("Snowflake Account  (e.g. myorg-myaccount)", "account"),
        "user"     : _prompt("Snowflake Username", "user"),
        "password" : _prompt("Snowflake Password", "password", secret=True),
        "warehouse": _prompt("Warehouse          (e.g. COMPUTE_WH)", "warehouse"),
        "database" : _prompt("Database           (e.g. HCE_DB)", "database"),
        "schema"   : _prompt("Schema             (e.g. STAR_RATINGS)", "schema"),
        "role"     : _prompt("Role               (optional, press Enter to skip)", "role"),
    }

    with open(config_path, "w") as f:
        json.dump(cfg, f, indent=2)

    print()
    print(f"  Credentials saved to {config_path}")
    print("  You can re-run --setup anytime to update them.")
    print("=" * 62)
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Download, process, and upload CMS Star Ratings Data Tables to Snowflake",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Auto-download + process + upload
  py -3.11 process_star_ratings.py --auto --upload

  # Use existing ZIP + upload
  py -3.11 process_star_ratings.py --zip ~/Downloads/2026-star-ratings-data-tables.zip --upload

  # Upload only (re-use previously processed outputs)
  py -3.11 process_star_ratings.py --upload --no-process

Snowflake config (env vars or edit SNOWFLAKE_CONFIG in script):
  export SNOWFLAKE_ACCOUNT="myorg-myaccount"
  export SNOWFLAKE_USER="my_user"
  export SNOWFLAKE_PASSWORD="my_password"
  export SNOWFLAKE_WAREHOUSE="MY_WH"
  export SNOWFLAKE_DATABASE="MY_DB"
  export SNOWFLAKE_SCHEMA="MY_SCHEMA"
  export SNOWFLAKE_ROLE="MY_ROLE"     # optional
        """
    )
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--auto",       action="store_true", help="Auto-download latest ZIP from CMS")
    src.add_argument("--zip",        metavar="PATH",      help="Path to a downloaded ZIP file")
    src.add_argument("--url",        metavar="URL",       help="Direct URL to ZIP file")
    parser.add_argument("--upload",      action="store_true", help="Upload processed data to Snowflake")
    parser.add_argument("--no-process",  action="store_true", help="Skip processing; load existing output for upload")
    parser.add_argument("--setup",       action="store_true", help="Run interactive setup to save Snowflake credentials")
    parser.add_argument("--ma-dir",      metavar="PATH",      help="Path to MA_Contract_directory_2026_04.xlsx to upload")
    parser.add_argument("--cut-points",  metavar="ZIP_PATH",  help="Path to ZIP to extract and upload Part C and D Cut Points tables")
    parser.add_argument("--cap",          metavar="HTML_PATH", help="Path to Ad-Hoc CAP Summary Report HTML file to upload")
    parser.add_argument("--extra-tables", metavar="ZIP_PATH",  help="Upload CAI, Domain Stars, Summary Ratings, High/Low Performing from ZIP")
    parser.add_argument("--measures",     metavar="ZIP_PATH",  help="Upload 2027 Star Ratings Part C and Part D measure tables from ZIP")
    parser.add_argument("--cap-info",     metavar="XLSX_PATH", help="Upload CAP letter contact info from Excel to CAP_INFO table")
    args = parser.parse_args()

    if args.setup:
        run_setup()
        sys.exit(0)

    if not args.auto and not args.zip and not args.url and not args.no_process and not args.ma_dir and not args.cut_points and not args.cap and not args.extra_tables and not args.measures and not args.cap_info:
        parser.print_help()
        sys.exit(1)

    tmp_dir  = None
    results  = None
    crosswks = None

    try:
        # Only run ZIP processing if a ZIP source was provided
        if args.auto or args.zip or args.url or args.no_process:
            if args.no_process:
                if not args.upload:
                    print("ERROR: --no-process requires --upload"); sys.exit(1)
                print("\n[LOAD] Reading existing output files…")
                results, crosswks = load_existing_outputs()
            else:
                zip_path = None
                if args.auto:
                    tmp_dir  = Path(tempfile.mkdtemp(prefix="cms_stars_"))
                    zip_path = auto_download(tmp_dir)
                elif args.zip:
                    zip_path = Path(args.zip)
                    if not zip_path.exists():
                        print(f"ERROR: File not found: {zip_path}"); sys.exit(1)
                elif args.url:
                    tmp_dir  = Path(tempfile.mkdtemp(prefix="cms_stars_"))
                    session  = requests.Session()
                    session.headers.update(BROWSER_HEADERS)
                    session.headers["Referer"] = CMS_PAGE_URL
                    print(f"\n[DOWNLOAD] {args.url}")
                    zip_path = _stream_download(session, args.url, tmp_dir)

                results, crosswks = process_zip(zip_path)

            # Upload Star Ratings to Snowflake
            if args.upload:
                snowflake_upload(results, crosswks)

    finally:
        if tmp_dir and tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # Handle MA Contract Directory upload
    if args.ma_dir:
        ma_path = Path(args.ma_dir)
        # If path is a folder, find the xlsx inside it
        if ma_path.is_dir():
            xlsx_files = list(ma_path.glob("*.xlsx"))
            if not xlsx_files:
                print(f"ERROR: No .xlsx file found in folder: {ma_path}")
                sys.exit(1)
            ma_path = xlsx_files[0]
            print(f"  Found file: {ma_path.name}")
        elif not ma_path.exists():
            print(f"ERROR: File not found: {ma_path}")
            sys.exit(1)
        _validate_sf_config(SNOWFLAKE_CONFIG)
        import snowflake.connector
        cfg = SNOWFLAKE_CONFIG
        db = cfg["database"].upper()
        schema = cfg["schema"].upper()
        conn_kwargs = {
            "account": cfg["account"], "user": cfg["user"],
            "password": cfg["password"], "warehouse": cfg["warehouse"],
            "database": db, "schema": schema,
        }
        if cfg.get("role"): conn_kwargs["role"] = cfg["role"]
        print("\n  Connecting to Snowflake for MA Directory upload...")
        con = snowflake.connector.connect(**conn_kwargs)
        try:
            cur = con.cursor()
            cur.execute(f'USE WAREHOUSE "{cfg["warehouse"]}"')
            cur.close()
            process_ma_directory(ma_path, con, db, schema)
        finally:
            con.close()

    # Handle Cut Points upload
    if args.cut_points:
        cp_path = Path(args.cut_points)
        if not cp_path.exists():
            print(f"ERROR: File not found: {cp_path}")
            sys.exit(1)
        _validate_sf_config(SNOWFLAKE_CONFIG)
        import snowflake.connector
        cfg = SNOWFLAKE_CONFIG
        db = cfg["database"].upper()
        schema = cfg["schema"].upper()
        conn_kwargs = {
            "account": cfg["account"], "user": cfg["user"],
            "password": cfg["password"], "warehouse": cfg["warehouse"],
            "database": db, "schema": schema,
        }
        if cfg.get("role"): conn_kwargs["role"] = cfg["role"]
        print("\n  Connecting to Snowflake for Cut Points upload...")
        con = snowflake.connector.connect(**conn_kwargs)
        try:
            cur = con.cursor()
            cur.execute(f'USE WAREHOUSE "{cfg["warehouse"]}"')
            cur.close()
            process_cut_points(cp_path, con, db, schema)
        finally:
            con.close()

    # Handle CAP Summary upload
    if args.cap:
        cap_path = Path(args.cap)
        if not cap_path.exists():
            print(f"ERROR: File not found: {cap_path}")
            sys.exit(1)
        _validate_sf_config(SNOWFLAKE_CONFIG)
        import snowflake.connector
        cfg = SNOWFLAKE_CONFIG
        db = cfg["database"].upper()
        schema = cfg["schema"].upper()
        conn_kwargs = {
            "account": cfg["account"], "user": cfg["user"],
            "password": cfg["password"], "warehouse": cfg["warehouse"],
            "database": db, "schema": schema,
        }
        if cfg.get("role"): conn_kwargs["role"] = cfg["role"]
        print("\n  Connecting to Snowflake for CAP Summary upload...")
        con = snowflake.connector.connect(**conn_kwargs)
        try:
            cur = con.cursor()
            cur.execute(f'USE WAREHOUSE "{cfg["warehouse"]}"')
            cur.close()
            process_cap_summary(cap_path, con, db, schema)
        finally:
            con.close()

    # Handle extra tables upload
    if args.extra_tables:
        et_path = Path(args.extra_tables)
        if not et_path.exists():
            print(f"ERROR: File not found: {et_path}")
            sys.exit(1)
        _validate_sf_config(SNOWFLAKE_CONFIG)
        import snowflake.connector
        cfg = SNOWFLAKE_CONFIG
        db = cfg["database"].upper()
        schema = cfg["schema"].upper()
        conn_kwargs = {
            "account": cfg["account"], "user": cfg["user"],
            "password": cfg["password"], "warehouse": cfg["warehouse"],
            "database": db, "schema": schema,
        }
        if cfg.get("role"): conn_kwargs["role"] = cfg["role"]
        print("\n  Connecting to Snowflake for extra tables upload...")
        con = snowflake.connector.connect(**conn_kwargs)
        try:
            cur = con.cursor()
            cur.execute(f'USE WAREHOUSE "{cfg["warehouse"]}"')
            cur.close()
            process_extra_tables(et_path, con, db, schema)
        finally:
            con.close()

    # Handle 2027 Star Ratings measures upload
    if args.measures:
        m_path = Path(args.measures)
        if not m_path.exists():
            print(f"ERROR: File not found: {m_path}")
            sys.exit(1)
        _validate_sf_config(SNOWFLAKE_CONFIG)
        import snowflake.connector
        cfg = SNOWFLAKE_CONFIG
        db = cfg["database"].upper()
        schema = cfg["schema"].upper()
        conn_kwargs = {
            "account": cfg["account"], "user": cfg["user"],
            "password": cfg["password"], "warehouse": cfg["warehouse"],
            "database": db, "schema": schema,
        }
        if cfg.get("role"): conn_kwargs["role"] = cfg["role"]
        print("\n  Connecting to Snowflake for Star Measures upload...")
        con = snowflake.connector.connect(**conn_kwargs)
        try:
            cur = con.cursor()
            cur.execute(f'USE WAREHOUSE "{cfg["warehouse"]}"')
            cur.close()
            process_star_measures(m_path, con, db, schema)
        finally:
            con.close()

    # Handle CAP Info upload
    if args.cap_info:
        ci_path = Path(args.cap_info)
        if not ci_path.exists():
            print(f"ERROR: File not found: {ci_path}")
            sys.exit(1)
        _validate_sf_config(SNOWFLAKE_CONFIG)
        import snowflake.connector
        cfg = SNOWFLAKE_CONFIG
        db = cfg["database"].upper()
        schema = cfg["schema"].upper()
        conn_kwargs = {
            "account": cfg["account"], "user": cfg["user"],
            "password": cfg["password"], "warehouse": cfg["warehouse"],
            "database": db, "schema": schema,
        }
        if cfg.get("role"): conn_kwargs["role"] = cfg["role"]
        print("\n  Connecting to Snowflake for CAP Info upload...")
        con = snowflake.connector.connect(**conn_kwargs)
        try:
            cur = con.cursor()
            cur.execute(f'USE WAREHOUSE "{cfg["warehouse"]}"')
            cur.close()
            process_cap_info(ci_path, con, db, schema)
        finally:
            con.close()

    print(f"\n{SEP}")
    print("  All done!")
    print(SEP + "\n")


if __name__ == "__main__":
    main()
