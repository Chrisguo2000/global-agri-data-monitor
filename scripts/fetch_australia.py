#!/usr/bin/env python3
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime as dt
import html
import io
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw/australia"
OUTPUT_TIMEZONE = os.environ.get("OUTPUT_TIMEZONE", "Asia/Shanghai")

MLA_REPORTS = {
    "eyci": (
        "https://app.nlrsreports.mla.com.au/indicators/eyci/",
        "eyci/MLA_eastern_young_cattle_all_dates.csv",
    ),
    "nyci": (
        "https://app.nlrsreports.mla.com.au/indicators/nyci/",
        "nyci/MLA_national_young_cattle_all_dates.csv",
    ),
    "indicators": (
        "https://app.nlrsreports.mla.com.au/statistics/nlrs-indicators/",
        "MLA_nlrs_indicators_report.csv",
    ),
}

ABS_LIVESTOCK_URL = "https://www.abs.gov.au/statistics/industry/agriculture/livestock-products/latest-release"
ABS_CPI_URL = "https://www.abs.gov.au/statistics/economy/price-indexes-and-inflation/consumer-price-index-australia/latest-release"
PORK_REPORTS_URL = "https://australianpork.com.au/market-reports/eyes-and-ears-reports"
MLA_STATS_API_BASE = "https://api-mlastatistics.mla.com.au"
MLA_STATS_API_DEFAULT_FROM_DATE = "2000-01-01"
MLA_STATS_API_DEFAULT_HIGH_VOLUME_FROM_DATE = "2021-01-01"
MLA_STATS_API_INCREMENTAL_LOOKBACK_MONTHS = max(
    0,
    int(os.environ.get("AUSTRALIA_MLA_INCREMENTAL_LOOKBACK_MONTHS", "18") or "18"),
)
MLA_STATS_API_PAGE_SIZE = 100
MLA_STATS_API_RETRY_ATTEMPTS = max(1, int(os.environ.get("AUSTRALIA_MLA_API_RETRY_ATTEMPTS", "6") or "6"))
MLA_STATS_API_RETRY_SECONDS = max(1, int(os.environ.get("AUSTRALIA_MLA_API_RETRY_SECONDS", "3") or "3"))
MLA_STATS_API_WORKERS = max(1, int(os.environ.get("AUSTRALIA_MLA_API_WORKERS", "4") or "4"))

try:
    sys.stdout.reconfigure(line_buffering=True)
except AttributeError:
    pass

MLA_POWERBI_REPORTS = {
    "retail_meat_prices": (
        "https://app.nlrsreports.mla.com.au/statistics/aus-retail-meat-prices/",
        "powerbi/MLA_abs_retail_meat_prices.csv",
    ),
}

LONG_COLUMNS = ["国家", "品类", "指标", "日期", "数值", "单位", "频率", "来源", "范围", "说明"]

ABS_TABLES = {
    "7215003.xlsx": "牛屠宰量(千头,季度)",
    "7215007.xlsx": "猪屠宰量(千头,季度)",
    "7215008.xlsx": "鸡屠宰量(千头,季度)",
    "7215009.xlsx": "牛肉产量(吨,季度)",
    "7215013.xlsx": "猪肉产量(吨,季度)",
    "7215015.xlsx": "鸡肉产量(吨,季度)",
}

FEED_GRAIN_LABELS = {
    "Feed Wheat": "饲料小麦澳洲交付价月均(A$/t)",
    "Feed Barley": "饲料大麦澳洲交付价月均(A$/t)",
    "Sorghum": "高粱澳洲交付价月均(A$/t)",
    "Soy meal": "豆粕澳洲交付价月均(A$/t)",
    "Canola meal": "油菜粕澳洲交付价月均(A$/t)",
    "Cotton seed": "棉籽澳洲交付价月均(A$/t)",
    "Triticale": "小黑麦澳洲交付价月均(A$/t)",
    "Feed Oats": "饲料燕麦澳洲交付价月均(A$/t)",
}

SPECIES_CATEGORY = {
    "Beef": "牛",
    "Beef And Veal": "牛",
    "Cattle": "牛",
    "Cattle (Excl. Calves)": "牛",
    "Calves": "牛",
    "Cows And Heifers": "牛",
    "Bulls, Bullocks And Steers": "牛",
    "Pork": "猪",
    "Pigs": "猪",
    "Chicken": "鸡",
    "Chickens": "鸡",
    "Lamb": "羊",
    "Lambs": "羊",
    "Sheep": "羊",
    "Mutton": "羊",
    "Goat": "羊",
    "Goats": "羊",
    "Total Red Meat": "牛",
}

MLA_INDICATOR_SKIP_IDS = {
    0,   # EYCI is kept from the sparse all-dates export to avoid dense calendar head-count duplication.
    3,   # Feeder Steer already comes from NLRS indicators export.
    4,   # Heavy Steer already comes from NLRS indicators export.
    14,  # NYCI is kept from the sparse all-dates export to avoid dense calendar head-count duplication.
}


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "").strip())


def species_to_category(value):
    return SPECIES_CATEGORY.get(clean_text(value), "其他")


def display_unit(value, fallback=""):
    unit = clean_text(value)
    if unit == "000":
        return "千头"
    if unit.lower() == "tonnes":
        return "吨"
    return unit or fallback


def finite_number(value):
    return pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]


def financial_year_to_date(value):
    text = clean_text(value)
    match = re.match(r"(\d{4})-(\d{2})$", text)
    if not match:
        return pd.NaT
    start_year = int(match.group(1))
    end_suffix = int(match.group(2))
    end_year = (start_year // 100) * 100 + end_suffix
    if end_year < start_year:
        end_year += 100
    return pd.Timestamp(year=end_year, month=6, day=30)


def format_date(value):
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def baseline_incremental_start_date(baseline_path):
    if not baseline_path or not baseline_path.exists():
        return None
    try:
        baseline = pd.read_csv(baseline_path, encoding="utf-8-sig", usecols=["日期"])
    except Exception as exc:
        print(f"[BASELINE WARN] cannot read baseline dates from {baseline_path}: {exc}", file=sys.stderr)
        return None
    dates = pd.to_datetime(baseline["日期"], errors="coerce").dropna()
    if dates.empty:
        return None
    latest = dates.max().to_period("M").to_timestamp()
    start = latest - pd.DateOffset(months=MLA_STATS_API_INCREMENTAL_LOOKBACK_MONTHS)
    return format_date(start)


def resolve_mla_api_windows(baseline_path, full_history):
    env_from = os.environ.get("AUSTRALIA_MLA_API_FROM_DATE")
    env_high_volume_from = os.environ.get("AUSTRALIA_MLA_HIGH_VOLUME_FROM_DATE")
    incremental_start = None if full_history else baseline_incremental_start_date(baseline_path)
    api_from_date = env_from or incremental_start or MLA_STATS_API_DEFAULT_FROM_DATE
    high_volume_from_date = (
        env_high_volume_from
        or incremental_start
        or MLA_STATS_API_DEFAULT_HIGH_VOLUME_FROM_DATE
    )
    print(
        "[MLA API WINDOW] "
        f"api_from={api_from_date}; high_volume_from={high_volume_from_date}; "
        f"lookback_months={MLA_STATS_API_INCREMENTAL_LOOKBACK_MONTHS}; "
        f"env_override={bool(env_from or env_high_volume_from)}"
    )
    return api_from_date, high_volume_from_date


def session():
    client = requests.Session()
    client.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            )
        }
    )
    return client


def month_start(values):
    return pd.to_datetime(values).dt.to_period("M").dt.to_timestamp()


def current_month_start():
    now = dt.datetime.now(dt.UTC).astimezone(ZoneInfo(OUTPUT_TIMEZONE))
    return pd.Timestamp(year=now.year, month=now.month, day=1)


def drop_current_incomplete_month(df, month_col="月份"):
    if df.empty or month_col not in df.columns:
        return df
    out = df.copy()
    out[month_col] = pd.to_datetime(out[month_col], errors="coerce")
    return out[out[month_col] < current_month_start()].reset_index(drop=True)


def request_text(client, url):
    response = client.get(url, timeout=90)
    response.raise_for_status()
    return response.text


def download_file(client, url, out_path, min_bytes=1000):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    response = client.get(url, timeout=120)
    response.raise_for_status()
    content = response.content
    if len(content) < min_bytes:
        raise RuntimeError(f"Downloaded file is too small: {url} ({len(content)} bytes)")
    out_path.write_bytes(content)
    return out_path


def xlsx_links(page_html, base_url):
    links = []
    for match in re.finditer(r'href=["\']([^"\']+\.xlsx(?:\?[^"\']*)?)["\']', page_html, flags=re.I):
        href = html.unescape(match.group(1))
        links.append(urljoin(base_url, href))
    return sorted(set(links))


def pdf_links(page_html, base_url):
    links = set()
    patterns = [
        r'https://australianpork\.com\.au/sites/default/files/[^"\s<>]+\.pdf',
        r'href=["\']([^"\']+\.pdf(?:\?[^"\']*)?)["\']',
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, page_html, flags=re.I):
            href = match.group(1) if match.lastindex else match.group(0)
            links.add(urljoin(base_url, html.unescape(href)))
    return sorted(links, key=issue_sort_key)


def issue_sort_key(url):
    name = unquote(Path(urlparse(url).path).name)
    match = re.search(r"(?:Issue\s*#?|#)\s*(\d{3,5})", name, flags=re.I)
    return int(match.group(1)) if match else -1


def filename_for_url(url):
    name = unquote(Path(urlparse(url).path).name)
    name = re.sub(r"[^A-Za-z0-9._# -]+", "_", name).strip()
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return name


def run_mla_exports(raw_dir, full_history):
    node = os.environ.get("NODE_BINARY") or "node"
    tool = ROOT / "tools/export_mla_powerbi.cjs"
    outputs = {}
    for name, (url, relative_out) in MLA_REPORTS.items():
        # The report default/current window can export dense calendar rows for
        # EYCI/NYCI, including non-report carry-forward dates. The all-dates
        # visual export keeps the sparse MLA report-observation grain used by
        # the historical database and is small enough to refresh every run.
        mode = "--all-dates"
        out_path = raw_dir / relative_out
        out_path.parent.mkdir(parents=True, exist_ok=True)
        command = [node, str(tool), mode, url, str(out_path)]
        print(f"[MLA] exporting {name} ({mode}): {url}")
        subprocess.run(command, cwd=ROOT, check=True)
        outputs[name] = out_path
    return outputs


def run_mla_powerbi_exports(raw_dir):
    node = os.environ.get("NODE_BINARY") or "node"
    tool = ROOT / "tools/export_mla_powerbi.cjs"
    outputs = {}
    for name, (url, relative_out) in MLA_POWERBI_REPORTS.items():
        out_path = raw_dir / relative_out
        out_path.parent.mkdir(parents=True, exist_ok=True)
        command = [node, str(tool), "--current", url, str(out_path)]
        print(f"[MLA PowerBI] exporting {name}: {url}")
        subprocess.run(command, cwd=ROOT, check=True)
        outputs[name] = out_path
    return outputs


def download_abs_tables(client, raw_dir):
    livestock_html = request_text(client, ABS_LIVESTOCK_URL)
    livestock_links = xlsx_links(livestock_html, ABS_LIVESTOCK_URL)
    downloaded = {}
    for file_name in ABS_TABLES:
        match = next((url for url in livestock_links if Path(urlparse(url).path).name.lower() == file_name.lower()), None)
        if not match:
            raise RuntimeError(f"ABS livestock table not found on latest-release page: {file_name}")
        downloaded[file_name] = download_file(client, match, raw_dir / "abs" / file_name)
        print(f"[ABS] {file_name}")

    cpi_html = request_text(client, ABS_CPI_URL)
    cpi_links = xlsx_links(cpi_html, ABS_CPI_URL)
    if not cpi_links:
        raise RuntimeError("ABS CPI latest-release page has no xlsx links")
    for idx, url in enumerate(cpi_links, 1):
        candidate = download_file(client, url, raw_dir / "cpi" / f"candidate_{idx}.xlsx")
        if cpi_table_has_food_indexes(candidate):
            out = raw_dir / "cpi" / "monthly_cpi_table1.xlsx"
            out.write_bytes(candidate.read_bytes())
            downloaded["monthly_cpi_table1.xlsx"] = out
            print(f"[ABS CPI] {Path(urlparse(url).path).name} -> {out.name}")
            break
    if "monthly_cpi_table1.xlsx" not in downloaded:
        raise RuntimeError("No ABS CPI xlsx contains Beef/Pork/Poultry/Milk/Eggs index columns")
    return downloaded


def cpi_table_has_food_indexes(path):
    try:
        data = pd.read_excel(path, sheet_name="Data1", header=None, engine="openpyxl", nrows=2)
    except Exception:
        return False
    headers = set(data.iloc[0].astype(str).str.strip())
    required = {
        "Index Numbers ;  Beef and veal ;  Australia ;",
        "Index Numbers ;  Pork ;  Australia ;",
        "Index Numbers ;  Poultry ;  Australia ;",
        "Index Numbers ;  Milk ;  Australia ;",
        "Index Numbers ;  Eggs ;  Australia ;",
    }
    return required.issubset(headers)


def download_pork_reports(client, raw_dir, max_pdfs):
    page_html = request_text(client, PORK_REPORTS_URL)
    links = pdf_links(page_html, PORK_REPORTS_URL)
    if not links:
        raise RuntimeError("Australian Pork Eyes and Ears page has no PDF links")
    selected = links if max_pdfs <= 0 else links[-max_pdfs:]
    out_dir = raw_dir / "pork_pdfs"
    out_dir.mkdir(parents=True, exist_ok=True)
    downloaded = []
    failed = []
    for idx, url in enumerate(selected, 1):
        out = out_dir / filename_for_url(url)
        try:
            download_file(client, url, out, min_bytes=20_000)
            downloaded.append(str(out))
            print(f"[PORK] {idx}/{len(selected)} {out.name}")
            time.sleep(0.1)
        except Exception as exc:
            failed.append({"url": url, "error": str(exc)})
            print(f"[PORK FAIL] {url}: {exc}", file=sys.stderr)
    manifest = {"source": PORK_REPORTS_URL, "links": links, "downloaded": downloaded, "failed": failed}
    (raw_dir / "australian_pork_pdf_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if not downloaded:
        raise RuntimeError("No Australian Pork PDFs downloaded")
    return [Path(item) for item in downloaded]


def mla_api_get(client, endpoint, params=None):
    url = f"{MLA_STATS_API_BASE}{endpoint}"
    params = {key: value for key, value in (params or {}).items() if value not in (None, "", [])}
    last_error = None
    for attempt in range(MLA_STATS_API_RETRY_ATTEMPTS):
        try:
            response = client.get(url, params=params, timeout=120)
            response.raise_for_status()
            payload = response.json()
            if "data" not in payload and "Message" in payload:
                raise RuntimeError(payload["Message"])
            return payload
        except Exception as exc:
            last_error = exc
            if attempt < MLA_STATS_API_RETRY_ATTEMPTS - 1:
                wait = min(60, MLA_STATS_API_RETRY_SECONDS * (2 ** attempt))
                print(
                    f"[MLA API RETRY] {endpoint} {params}: {exc}; "
                    f"retry {attempt + 2}/{MLA_STATS_API_RETRY_ATTEMPTS} in {wait}s",
                    file=sys.stderr,
                )
                time.sleep(wait)
    raise RuntimeError(f"MLA Statistics API failed: {endpoint} {params}: {last_error}")


def fetch_mla_api_page(endpoint, params, page):
    worker_client = session()
    payload = mla_api_get(worker_client, endpoint, {**(params or {}), "page": page})
    return page, payload.get("data") or []


def fetch_mla_api_dataset(client, raw_dir, name, endpoint, params=None):
    params = params or {}
    payload = mla_api_get(client, endpoint, {**params, "page": 1})
    first_batch = payload.get("data") or []
    total = int(payload.get("total number rows") or len(first_batch))
    if len(first_batch) < MLA_STATS_API_PAGE_SIZE or len(first_batch) >= total:
        total_pages = 1
    else:
        total_pages = max(1, (total + MLA_STATS_API_PAGE_SIZE - 1) // MLA_STATS_API_PAGE_SIZE)
    records_by_page = {1: first_batch}

    if total_pages > 1:
        workers = min(MLA_STATS_API_WORKERS, total_pages - 1)
        print(f"[MLA API] {name}: {total} rows across {total_pages} pages; workers={workers}")
        done_pages = 1
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(fetch_mla_api_page, endpoint, params, page): page
                for page in range(2, total_pages + 1)
            }
            for future in as_completed(futures):
                page, batch = future.result()
                records_by_page[page] = batch
                done_pages += 1
                if done_pages == total_pages or done_pages % 10 == 0:
                    print(f"[MLA API] {name}: page {done_pages}/{total_pages}")

    records = []
    for page in sorted(records_by_page):
        records.extend(records_by_page[page])
    if total and len(records) != total:
        print(
            f"[MLA API WARN] {name}: expected {total} rows, downloaded {len(records)} rows",
            file=sys.stderr,
        )

    out = raw_dir / "mla_stats_api" / f"{name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "endpoint": endpoint,
                "params": params,
                "totalRows": total,
                "rows": len(records),
                "data": records,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[MLA API] {name}: {len(records)} rows")
    return records


def fetch_mla_stats_api_raw(client, raw_dir, api_from_date, high_volume_from_date):
    data = {}
    data["report_list"] = fetch_mla_api_dataset(client, raw_dir, "report_list", "/report")
    indicators = fetch_mla_api_dataset(client, raw_dir, "indicator_list", "/indicator")
    data["indicator_list"] = indicators
    data["saleyard_list"] = fetch_mla_api_dataset(client, raw_dir, "saleyard_list", "/saleyard")

    data["report_1_exports"] = fetch_mla_api_dataset(
        client,
        raw_dir,
        "report_1_exports",
        "/report/1",
        {"fromDate": api_from_date},
    )

    herd_records = []
    current_year = dt.datetime.now(dt.UTC).astimezone(ZoneInfo(OUTPUT_TIMEZONE)).year
    herd_start_year = max(2000, pd.Timestamp(api_from_date).year - 1)
    for year in range(herd_start_year, current_year + 1):
        try:
            herd_records.extend(
                fetch_mla_api_dataset(
                    client,
                    raw_dir,
                    f"report_2_herd_{year}",
                    "/report/2",
                    {"year": year},
                )
            )
        except Exception as exc:
            print(f"[MLA API WARN] report_2_herd_{year}: {exc}", file=sys.stderr)
    data["report_2_herd"] = herd_records

    data["report_3_slaughter_production"] = fetch_mla_api_dataset(
        client,
        raw_dir,
        "report_3_slaughter_production",
        "/report/3",
        {"fromDate": api_from_date},
    )

    yarding_records = []
    for category in ["Cattle", "Lamb", "Sheep"]:
        yarding_records.extend(
            fetch_mla_api_dataset(
                client,
                raw_dir,
                f"report_4_yardings_{category.lower()}",
                "/report/4",
                {"fromDate": high_volume_from_date, "category": category},
            )
        )
    data["report_4_yardings"] = yarding_records

    indicator_records = []
    for item in indicators:
        indicator_id = int(item.get("indicator_id"))
        if indicator_id in MLA_INDICATOR_SKIP_IDS:
            continue
        indicator_records.extend(
            fetch_mla_api_dataset(
                client,
                raw_dir,
                f"report_5_indicator_{indicator_id}",
                "/report/5",
                {"fromDate": high_volume_from_date, "indicatorID": indicator_id},
            )
        )
    data["report_5_indicators"] = indicator_records

    global_cattle_records = []
    for country in ["AUS", "USA"]:
        global_cattle_records.extend(
            fetch_mla_api_dataset(
                client,
                raw_dir,
                f"report_7_global_cattle_{country.lower()}",
                "/report/7",
                {"fromDate": api_from_date, "countryID": country},
            )
        )
    data["report_7_global_cattle"] = global_cattle_records

    data["report_8_us_domestic_cattle"] = fetch_mla_api_dataset(
        client,
        raw_dir,
        "report_8_us_domestic_cattle",
        "/report/8",
        {"fromDate": api_from_date},
    )
    data["report_9_us_imported_beef"] = fetch_mla_api_dataset(
        client,
        raw_dir,
        "report_9_us_imported_beef",
        "/report/9",
        {"fromDate": api_from_date},
    )
    data["report_10_nlrs_slaughter"] = fetch_mla_api_dataset(
        client,
        raw_dir,
        "report_10_nlrs_slaughter",
        "/report/10",
        {"fromDate": high_volume_from_date},
    )
    return data


def load_mla_stats_api_raw(raw_dir):
    api_dir = raw_dir / "mla_stats_api"

    def load_one(name):
        path = api_dir / f"{name}.json"
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload.get("data") or []

    def load_many(pattern):
        records = []
        for path in sorted(api_dir.glob(pattern)):
            payload = json.loads(path.read_text(encoding="utf-8"))
            records.extend(payload.get("data") or [])
        return records

    return {
        "report_list": load_one("report_list"),
        "indicator_list": load_one("indicator_list"),
        "saleyard_list": load_one("saleyard_list"),
        "report_1_exports": load_one("report_1_exports"),
        "report_2_herd": load_many("report_2_herd_*.json"),
        "report_3_slaughter_production": load_one("report_3_slaughter_production"),
        "report_4_yardings": load_many("report_4_yardings_*.json"),
        "report_5_indicators": load_many("report_5_indicator_*.json"),
        "report_7_global_cattle": load_many("report_7_global_cattle_*.json"),
        "report_8_us_domestic_cattle": load_one("report_8_us_domestic_cattle"),
        "report_9_us_imported_beef": load_one("report_9_us_imported_beef"),
        "report_10_nlrs_slaughter": load_one("report_10_nlrs_slaughter"),
    }


def add_long_row(rows, country, category, metric, date, value, unit, frequency, source, scope, note):
    value = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(value):
        return
    rows.append(
        {
            "国家": country,
            "品类": category,
            "指标": metric,
            "日期": date,
            "数值": float(value),
            "单位": unit,
            "频率": frequency,
            "来源": source,
            "范围": scope,
            "说明": note,
        }
    )


def monthly_group(records, date_col, value_col, group_cols, aggfunc):
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    if df.empty:
        return df
    df["月份"] = month_start(pd.to_datetime(df[date_col], errors="coerce"))
    df["__value"] = pd.to_numeric(df[value_col], errors="coerce")
    df = df.dropna(subset=["月份", "__value"])
    if df.empty:
        return df
    return df.groupby(["月份", *group_cols], dropna=False, as_index=False)["__value"].agg(aggfunc)


def parse_mla_api_long(api_data):
    rows = []

    exports = monthly_group(
        api_data.get("report_1_exports"),
        "result_date",
        "weight_amt",
        ["country_desc", "meat_type_group_desc"],
        "sum",
    )
    for _, row in exports.iterrows():
        meat = clean_text(row["meat_type_group_desc"])
        destination = clean_text(row["country_desc"])
        add_long_row(
            rows,
            "au",
            species_to_category(meat),
            f"MLA出口{meat}至{destination}重量",
            row["月份"],
            row["__value"],
            "kg",
            "月度",
            "MLA Statistics API / DAFF",
            destination,
            "MLA Statistics API report/1 Australian Red Meat Exports；按目的地和肉类分组月度汇总，字段为weight_amt。",
        )

    herd = api_data.get("report_2_herd") or []
    if herd:
        herd_df = pd.DataFrame(herd)
        herd_df["月份"] = herd_df["financial_year"].map(financial_year_to_date)
        herd_df["__value"] = pd.to_numeric(herd_df["estimate_value"], errors="coerce")
        herd_df = herd_df.dropna(subset=["月份", "__value"])
        grouped = herd_df.groupby(
            ["月份", "region_desc", "subcategory_desc", "metric_desc"],
            dropna=False,
            as_index=False,
        )["__value"].last()
        for _, row in grouped.iterrows():
            subcategory = clean_text(row["subcategory_desc"])
            metric = clean_text(row["metric_desc"])
            region = clean_text(row["region_desc"])
            add_long_row(
                rows,
                "au",
                species_to_category(subcategory),
                f"MLA畜群{subcategory} {metric} {region}",
                row["月份"],
                row["__value"],
                "头",
                "年度",
                "MLA Statistics API / ABS",
                region,
                "MLA Statistics API report/2 Australian Herd and Flock Figures；按financial_year记录年度值。",
            )

    slaughter_production = monthly_group(
        api_data.get("report_3_slaughter_production"),
        "report_date",
        "value_amt",
        ["report_type", "category", "location_id", "unit_of_measure"],
        "last",
    )
    for _, row in slaughter_production.iterrows():
        report_type = clean_text(row["report_type"])
        category = clean_text(row["category"])
        location = clean_text(row["location_id"])
        unit = display_unit(row["unit_of_measure"])
        add_long_row(
            rows,
            "au",
            species_to_category(category),
            f"MLA ABS {report_type} {category} {location}",
            row["月份"],
            row["__value"],
            unit,
            "季度",
            "MLA Statistics API / ABS",
            location,
            "MLA Statistics API report/3 Australian Slaughter and Production；按动物类别和地区保留ABS季度值。",
        )

    yardings = monthly_group(
        api_data.get("report_4_yardings"),
        "result_date",
        "head_count",
        ["category_desc", "tranx_type_id"],
        "sum",
    )
    for _, row in yardings.iterrows():
        category = clean_text(row["category_desc"])
        tranx = clean_text(row["tranx_type_id"])
        add_long_row(
            rows,
            "au",
            species_to_category(category),
            f"MLA Saleyard Yardings {category} {tranx} 全国汇总",
            row["月份"],
            row["__value"],
            "头",
            "月度",
            "MLA Statistics API / NLRS",
            "澳洲saleyard汇总",
            "MLA Statistics API report/4 Australian Saleyard Yardings；默认看板汇总saleyard日度yardings到月度，不展开单个saleyard。",
        )

    indicators = monthly_group(
        api_data.get("report_5_indicators"),
        "calendar_date",
        "indicator_value",
        ["species_id", "indicator_desc", "indicator_units"],
        "mean",
    )
    for _, row in indicators.iterrows():
        species = clean_text(row["species_id"])
        desc = clean_text(row["indicator_desc"])
        unit = display_unit(row["indicator_units"])
        add_long_row(
            rows,
            "au",
            species_to_category(species),
            f"MLA {desc}",
            row["月份"],
            row["__value"],
            f"A¢/{unit.replace('c/', '')}" if unit.startswith("c/") else unit,
            "月度",
            "MLA Statistics API / NLRS",
            "澳洲",
            "MLA Statistics API report/5 Australian Livestock Indicators；日度指标按月平均。EYCI/NYCI成交头数不使用该密集日历序列。",
        )

    global_cattle = monthly_group(
        api_data.get("report_7_global_cattle"),
        "indicator_date",
        "indicator_value",
        ["country_code", "indicator_desc", "indicator_units", "currency_code"],
        "mean",
    )
    for _, row in global_cattle.iterrows():
        country_code = clean_text(row["country_code"]).lower()
        country = "us" if country_code == "usa" else "au"
        desc = clean_text(row["indicator_desc"])
        unit = display_unit(row["indicator_units"])
        currency = clean_text(row["currency_code"])
        add_long_row(
            rows,
            country,
            "牛",
            f"MLA全球牛价{country_code.upper()} {desc}",
            row["月份"],
            row["__value"],
            f"{currency} {unit}".strip(),
            "月度",
            "MLA Statistics API / Steiner/NLRS",
            country_code.upper(),
            "MLA Statistics API report/7 Global Cattle Prices；按国家和指标月均，来源含NLRS和Steiner Consulting。",
        )

    for report_key, source_label, source_note in [
        ("report_8_us_domestic_cattle", "MLA Statistics API / Steiner", "MLA Statistics API report/8 US Domestic Cattle Prices；按指标月均。"),
        ("report_9_us_imported_beef", "MLA Statistics API / Steiner", "MLA Statistics API report/9 US Imported Meat Prices；按指标月均。"),
    ]:
        grouped = monthly_group(
            api_data.get(report_key),
            "indicator_date",
            "indicator_value",
            ["indicator_name", "indicator_units"],
            "mean",
        )
        for _, row in grouped.iterrows():
            name = clean_text(row["indicator_name"])
            unit = display_unit(row["indicator_units"])
            add_long_row(
                rows,
                "us",
                "牛",
                f"MLA美国牛肉牛价 {name}",
                row["月份"],
                row["__value"],
                unit,
                "月度",
                source_label,
                "美国",
                source_note,
            )

    nlrs_slaughter = monthly_group(
        api_data.get("report_10_nlrs_slaughter"),
        "result_date",
        "slaughter_count",
        ["species_id"],
        "sum",
    )
    for _, row in nlrs_slaughter.iterrows():
        species = clean_text(row["species_id"])
        add_long_row(
            rows,
            "au",
            species_to_category(species),
            f"MLA NLRS Slaughter {species} 全国月度汇总",
            row["月份"],
            row["__value"],
            "头",
            "月度",
            "MLA Statistics API / NLRS",
            "澳洲",
            "MLA Statistics API report/10 NLRS Slaughter；州级周度屠宰数默认汇总为全国月度。",
        )

    if not rows:
        return pd.DataFrame(columns=LONG_COLUMNS)
    out = pd.DataFrame(rows, columns=LONG_COLUMNS)
    out["日期"] = pd.to_datetime(out["日期"], errors="coerce")
    out = out.dropna(subset=["日期", "数值"])
    out = drop_current_incomplete_month(out, "日期")
    out["日期"] = out["日期"].dt.strftime("%Y-%m-%d")
    return out


def read_mla_retail_prices(path):
    if not path.exists():
        return pd.DataFrame(columns=LONG_COLUMNS)
    df = pd.read_csv(path, encoding="utf-8-sig")
    required = {"Quarterly", "Category", "Average Value", "Units"}
    if not required.issubset(df.columns):
        raise RuntimeError(f"MLA retail export columns changed: {path}")
    rows = []
    for _, row in df.iterrows():
        category = clean_text(row["Category"])
        add_long_row(
            rows,
            "au",
            species_to_category(category),
            f"MLA零售肉价 {category}",
            pd.to_datetime(row["Quarterly"], errors="coerce"),
            row["Average Value"],
            display_unit(row["Units"]),
            "季度",
            "MLA PowerBI / ABS Retail Meat Prices",
            "澳洲零售",
            "MLA Statistics Australian Retail Meat Prices PowerBI导出；字段为Average Value，按ABS季度零售肉价展示。",
        )
    if not rows:
        return pd.DataFrame(columns=LONG_COLUMNS)
    out = pd.DataFrame(rows, columns=LONG_COLUMNS)
    out["日期"] = pd.to_datetime(out["日期"], errors="coerce")
    out = out.dropna(subset=["日期", "数值"])
    out = drop_current_incomplete_month(out, "日期")
    out["日期"] = out["日期"].dt.strftime("%Y-%m-%d")
    return out


def read_exported_csv(path):
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    header_idx = 0
    for idx, line in enumerate(lines):
        if ("Date" in line or "Weekly" in line) and "Indicator" in line:
            header_idx = idx
            break
    return pd.read_csv(io.StringIO("\n".join(lines[header_idx:])))


def read_young_cattle(path, value_col, label, head_label):
    df = read_exported_csv(path)
    df["日期"] = pd.to_datetime(df["Date"])
    df["月份"] = month_start(df["日期"])
    monthly_counts = df.groupby("月份")["日期"].size()
    dense_months = monthly_counts[monthly_counts > 18]
    if not dense_months.empty:
        months = ", ".join(month.strftime("%Y-%m") for month in dense_months.index[:6])
        raise RuntimeError(
            f"{path} contains dense calendar rows for young-cattle indicators ({months}); "
            "export MLA with --all-dates before monthly aggregation."
        )
    value = pd.to_numeric(df[value_col], errors="coerce")
    head = pd.to_numeric(df["Head Count"], errors="coerce")
    avg_head = pd.to_numeric(df["Average Price ($/Head)"], errors="coerce")
    out = pd.DataFrame(
        {
            "月份": df["月份"],
            label: value,
            head_label: head,
            f"{label}($/Head)": avg_head,
        }
    )
    monthly = out.groupby("月份", as_index=False).agg({label: "mean", head_label: "sum", f"{label}($/Head)": "mean"})
    return drop_current_incomplete_month(monthly)


def read_mla_weekly_indicators(path):
    df = read_exported_csv(path)
    df["月份"] = month_start(pd.to_datetime(df["Weekly"]))
    df["Indicator Value"] = pd.to_numeric(df["Indicator Value"], errors="coerce")
    keep = {
        "National Heavy Steer Indicator": "Heavy Steer月均(c/kg lwt)",
        "National Feeder Steer Indicator": "Feeder Steer月均(c/kg lwt)",
    }
    wide = (
        df[df["Indicator"].isin(keep)]
        .pivot_table(index="月份", columns="Indicator", values="Indicator Value", aggfunc="mean")
        .rename(columns=keep)
        .reset_index()
    )
    return drop_current_incomplete_month(wide)


def read_abs_total_series(raw_dir, file_name, label):
    path = raw_dir / "abs" / file_name
    df = pd.read_excel(path, sheet_name="Data1", header=None, engine="openpyxl")
    dates = pd.to_datetime(df.iloc[10:, 0], errors="coerce")
    values = pd.to_numeric(df.iloc[10:, 1], errors="coerce")
    return pd.DataFrame({"月份": month_start(dates), label: values}).dropna(subset=["月份", label])


def read_cpi_index(raw_dir, mapping):
    path = raw_dir / "cpi" / "monthly_cpi_table1.xlsx"
    if not path.exists():
        legacy_path = raw_dir / "cpi" / "640103.xlsx"
        if legacy_path.exists():
            path = legacy_path
    data = pd.read_excel(path, sheet_name="Data1", header=None, engine="openpyxl")
    dates = pd.to_datetime(data.iloc[10:, 0], errors="coerce")
    out = pd.DataFrame({"月份": month_start(dates)})
    headers = data.iloc[0].map(lambda value: "" if pd.isna(value) else str(value))
    for source_name, out_name in mapping.items():
        target = f"Index Numbers ;  {source_name} ;  Australia ;"
        matches = [idx for idx, value in headers.items() if value.strip() == target]
        if not matches:
            continue
        out[out_name] = pd.to_numeric(data.iloc[10:, matches[0]], errors="coerce").to_numpy()
    value_columns = [column for column in out.columns if column != "月份"]
    return out.dropna(how="all", subset=value_columns)


def first_nat_buyer_line(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        if not line.startswith("NAT "):
            continue
        nums = re.findall(r"-?\d+(?:\.\d+)?", line)
        if len(nums) >= 10:
            return [float(item) for item in nums[:10]]
    return None


def extract_week_ending_date(text, file_name):
    date_match = re.search(r"W/?E\.?\s*(\d{1,2})[./](\d{1,2})[./]?(\d{2,4})", text, flags=re.I)
    if not date_match:
        date_match = re.search(r"W/?E\.?\s*(\d{2})(\d{2})(\d{4})", file_name, flags=re.I)
    if not date_match:
        return None
    day, month, year = date_match.groups()
    year = int(year)
    if year < 100:
        year += 2000
    return pd.Timestamp(year=year, month=int(month), day=int(day))


def read_pdf_text(path):
    reader = PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def parse_pork_pdf(path):
    try:
        text = read_pdf_text(path)
    except Exception:
        return None
    report_date = extract_week_ending_date(text, path.name)
    if report_date is None:
        return None
    nums = first_nat_buyer_line(text)
    if not nums:
        return None
    return {
        "日期": report_date,
        "猪价 Buyers NAT 60.1-75kg平均(c/kg HSCW)": nums[8],
        "猪价 Buyers NAT 60.1-75kg最高(c/kg HSCW)": nums[3],
        "猪价周变化(c/kg)": nums[9],
    }


def parse_feed_grain_pdf(path):
    try:
        text = read_pdf_text(path)
    except Exception:
        return None
    report_date = extract_week_ending_date(text, path.name)
    if report_date is None:
        return None
    start = text.find("Weekly Grain Table")
    if start < 0:
        return None
    end = text.find("DD =", start)
    table_text = text[start : end if end > start else start + 5000]
    values_by_label = {label: [] for label in FEED_GRAIN_LABELS.values()}
    for raw_line in table_text.splitlines():
        line = re.sub(r"\s+", " ", raw_line.strip())
        for source_name, output_label in FEED_GRAIN_LABELS.items():
            if not line.startswith(f"{source_name} "):
                continue
            tokens = re.findall(r"N/?Q|-?\d+(?:\.\d+)?", line[len(source_name) :], flags=re.I)
            parsed = []
            for token in tokens:
                parsed.append(None if re.match(r"N/?Q", token, flags=re.I) else float(token))
            tw_values = [value for idx, value in enumerate(parsed) if idx % 3 == 1 and value and value > 0]
            values_by_label[output_label].extend(tw_values)
    record = {"日期": report_date}
    for label, values in values_by_label.items():
        if values:
            record[label] = sum(values) / len(values)
    return record if len(record) > 1 else None


def read_pork_prices(pdfs):
    records = [record for path in pdfs if (record := parse_pork_pdf(path))]
    if not records:
        return pd.DataFrame(columns=["月份"])
    df = pd.DataFrame(records).drop_duplicates("日期").sort_values("日期")
    df["月份"] = month_start(df["日期"])
    monthly = (
        df.groupby("月份", as_index=False)
        .agg(
            {
                "猪价 Buyers NAT 60.1-75kg平均(c/kg HSCW)": "mean",
                "猪价 Buyers NAT 60.1-75kg最高(c/kg HSCW)": "mean",
                "猪价周变化(c/kg)": "mean",
            }
        )
        .sort_values("月份")
    )
    return drop_current_incomplete_month(monthly)


def read_feed_grain_prices(pdfs):
    records = [record for path in pdfs if (record := parse_feed_grain_pdf(path))]
    if not records:
        return pd.DataFrame(columns=["月份"])
    df = pd.DataFrame(records).drop_duplicates("日期").sort_values("日期")
    df["月份"] = month_start(df["日期"])
    value_columns = [label for label in FEED_GRAIN_LABELS.values() if label in df.columns]
    monthly = df.groupby("月份", as_index=False).agg({column: "mean" for column in value_columns}).sort_values("月份")
    return drop_current_incomplete_month(monthly)


def merge_monthly(frames):
    clean = [frame.copy() for frame in frames if frame is not None and not frame.empty]
    if not clean:
        return pd.DataFrame(columns=["月份"])
    out = clean[0]
    for frame in clean[1:]:
        out = out.merge(frame, on="月份", how="outer")
    return out.sort_values("月份").reset_index(drop=True)


def build_cattle_monthly(raw_dir):
    eyci = read_young_cattle(
        raw_dir / "eyci" / "MLA_eastern_young_cattle_all_dates.csv",
        "Average Price (c/kg cwt)",
        "EYCI月均(c/kg cwt)",
        "EYCI月成交头数",
    )
    nyci = read_young_cattle(
        raw_dir / "nyci" / "MLA_national_young_cattle_all_dates.csv",
        "Average Price (c/kg lwt)",
        "NYCI月均(c/kg lwt)",
        "NYCI月成交头数",
    )
    weekly = read_mla_weekly_indicators(raw_dir / "MLA_nlrs_indicators_report.csv")
    slaughter = read_abs_total_series(raw_dir, "7215003.xlsx", "牛屠宰量(千头,季度)")
    beef_prod = read_abs_total_series(raw_dir, "7215009.xlsx", "牛肉产量(吨,季度)")
    cpi = read_cpi_index(raw_dir, {"Beef and veal": "牛肉小牛肉CPI指数"})
    return merge_monthly([eyci, nyci, weekly, slaughter, beef_prod, cpi])


def build_pork_monthly(raw_dir, pdfs):
    prices = read_pork_prices(pdfs)
    slaughter = read_abs_total_series(raw_dir, "7215007.xlsx", "猪屠宰量(千头,季度)")
    pork_prod = read_abs_total_series(raw_dir, "7215013.xlsx", "猪肉产量(吨,季度)")
    cpi = read_cpi_index(raw_dir, {"Pork": "猪肉CPI指数"})
    return merge_monthly([prices, slaughter, pork_prod, cpi])


def build_chicken_monthly(raw_dir):
    slaughter = read_abs_total_series(raw_dir, "7215008.xlsx", "鸡屠宰量(千头,季度)")
    prod = read_abs_total_series(raw_dir, "7215015.xlsx", "鸡肉产量(吨,季度)")
    cpi = read_cpi_index(raw_dir, {"Poultry": "禽肉CPI指数"})
    return merge_monthly([slaughter, prod, cpi])


def build_egg_monthly(raw_dir):
    return merge_monthly([read_cpi_index(raw_dir, {"Eggs": "鸡蛋CPI指数"})])


def build_milk_monthly(raw_dir):
    return merge_monthly([read_cpi_index(raw_dir, {"Milk": "牛奶CPI指数"})])


def build_feed_monthly(pdfs):
    return merge_monthly([read_feed_grain_prices(pdfs)])


def build_long(sheets):
    rows = []
    for category, df in sheets.items():
        for column in df.columns:
            if column == "月份":
                continue
            part = df[["月份", column]].dropna().rename(columns={"月份": "日期", column: "数值"})
            part["品类"] = category
            part["指标"] = column
            part["国家"] = "au"
            part["单位"] = ""
            part["频率"] = "月度"
            part["来源"] = ""
            part["范围"] = "澳洲"
            part["说明"] = ""
            rows.append(part)
    if not rows:
        raise RuntimeError("No Australia rows were produced")
    out = pd.concat(rows, ignore_index=True)[LONG_COLUMNS]
    out["日期"] = pd.to_datetime(out["日期"]).dt.strftime("%Y-%m-%d")
    out["数值"] = pd.to_numeric(out["数值"], errors="coerce")
    out = out.dropna(subset=["数值"]).sort_values(["品类", "指标", "日期"])
    return out


def cleanup_previous_outputs(output_dir):
    for path in output_dir.glob("澳洲农业数据_*_合并长表.csv"):
        path.unlink()


def find_latest_australia_csv(output_dir):
    candidates = list(output_dir.glob("澳洲农业数据_*_合并长表.csv"))
    if not candidates:
        return None
    return max(candidates, key=lambda path: (path.stat().st_mtime, path.name))


def merge_with_baseline(current, baseline_path):
    if not baseline_path or not baseline_path.exists():
        out = current.copy()
        for column in LONG_COLUMNS:
            if column not in out.columns:
                out[column] = ""
        out = drop_current_incomplete_month(out[LONG_COLUMNS].rename(columns={"日期": "月份"})).rename(columns={"月份": "日期"})
        out["日期"] = pd.to_datetime(out["日期"], errors="coerce").dt.strftime("%Y-%m-%d")
        return out.sort_values(["国家", "品类", "指标", "日期"]).reset_index(drop=True)
    baseline = pd.read_csv(baseline_path, encoding="utf-8-sig")
    expected = {"品类", "指标", "日期", "数值"}
    if not expected.issubset(baseline.columns):
        raise RuntimeError(f"澳洲 baseline CSV 字段不完整: {baseline_path}")
    for column in LONG_COLUMNS:
        if column not in baseline.columns:
            baseline[column] = ""
        if column not in current.columns:
            current[column] = ""
    baseline = baseline[LONG_COLUMNS].copy()
    current = current[LONG_COLUMNS].copy()
    combined = pd.concat([baseline, current], ignore_index=True)
    combined["日期"] = pd.to_datetime(combined["日期"], errors="coerce")
    combined["数值"] = pd.to_numeric(combined["数值"], errors="coerce")
    combined = combined.dropna(subset=["日期", "数值"])
    combined = combined[combined["日期"] < current_month_start()]
    combined["日期"] = combined["日期"].dt.strftime("%Y-%m-%d")
    combined = combined.drop_duplicates(["国家", "品类", "指标", "日期"], keep="last")
    return combined.sort_values(["国家", "品类", "指标", "日期"]).reset_index(drop=True)


def parse_args():
    parser = argparse.ArgumentParser(description="实时抓取澳洲农业公开数据并生成合并长表")
    parser.add_argument("--output-dir", default=str(ROOT / "data"), help="输出 CSV 目录")
    parser.add_argument("--raw-dir", default=str(RAW), help="实时下载源文件暂存目录")
    parser.add_argument("--baseline", help="上一版澳洲合并长表 CSV；不传则自动找 output-dir 下最新文件")
    parser.add_argument("--full-history", action="store_true", help="强制抓取 MLA 全历史并解析全部 Australian Pork PDF")
    parser.add_argument(
        "--pork-max-pdfs",
        type=int,
        default=None,
        help="Australian Pork PDF 下载数量；0 表示下载页面上全部 PDF；不传时首跑全量、有 baseline 时默认最新16期；显式传入或设置AUSTRALIA_PORK_MAX_PDFS时优先",
    )
    parser.add_argument("--skip-download", action="store_true", help="仅用 raw-dir 已有源文件重建长表")
    return parser.parse_args()


def main():
    args = parse_args()
    raw_dir = Path(args.raw_dir)
    if not raw_dir.is_absolute():
        raw_dir = ROOT / raw_dir
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    raw_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.baseline:
        baseline_path = Path(args.baseline)
        if not baseline_path.is_absolute():
            baseline_path = ROOT / baseline_path
    else:
        baseline_path = find_latest_australia_csv(output_dir)
    full_history = args.full_history or baseline_path is None
    pork_env = os.environ.get("AUSTRALIA_PORK_MAX_PDFS")
    if args.pork_max_pdfs is not None:
        pork_max_pdfs = args.pork_max_pdfs
    elif pork_env is not None:
        pork_max_pdfs = int(pork_env or "0")
    else:
        pork_max_pdfs = 0 if full_history else 16
    mode_text = "full-history" if full_history else "incremental"
    if baseline_path:
        print(f"[BASELINE] {baseline_path}")
    print(f"[MODE] {mode_text}; pork_max_pdfs={pork_max_pdfs}")

    client = session()
    if not args.skip_download:
        api_from_date, high_volume_from_date = resolve_mla_api_windows(baseline_path, full_history)
        run_mla_exports(raw_dir, full_history)
        run_mla_powerbi_exports(raw_dir)
        mla_api_data = fetch_mla_stats_api_raw(client, raw_dir, api_from_date, high_volume_from_date)
        download_abs_tables(client, raw_dir)
        pdfs = download_pork_reports(client, raw_dir, pork_max_pdfs)
    else:
        mla_api_data = load_mla_stats_api_raw(raw_dir)
        pdfs = sorted((raw_dir / "pork_pdfs").glob("*.pdf"))
        if not pdfs:
            raise RuntimeError(f"No pork PDFs in {raw_dir / 'pork_pdfs'}")

    sheets = {
        "牛": build_cattle_monthly(raw_dir),
        "猪": build_pork_monthly(raw_dir, pdfs),
        "鸡": build_chicken_monthly(raw_dir),
        "鸡蛋": build_egg_monthly(raw_dir),
        "生鲜乳": build_milk_monthly(raw_dir),
        "饲料": build_feed_monthly(pdfs),
    }
    out = build_long(sheets)
    mla_stats_long = parse_mla_api_long(mla_api_data)
    retail_long = read_mla_retail_prices(raw_dir / MLA_POWERBI_REPORTS["retail_meat_prices"][1])
    extras = [frame for frame in [mla_stats_long, retail_long] if frame is not None and not frame.empty]
    if extras:
        out = pd.concat([out, *extras], ignore_index=True)
    new_rows = len(out)
    out = merge_with_baseline(out, baseline_path)

    now = dt.datetime.now(dt.UTC).astimezone(ZoneInfo(OUTPUT_TIMEZONE))
    stamp = now.strftime("%Y-%m-%d_%H%M")
    cleanup_previous_outputs(output_dir)
    out_path = output_dir / f"澳洲农业数据_{stamp}_合并长表.csv"
    out.to_csv(out_path, index=False, encoding="utf-8-sig")
    latest_by_category = out.groupby("品类")["日期"].max().to_dict()
    print(
        json.dumps(
            {
                "output": str(out_path),
                "rows": len(out),
                "newRowsBeforeBaselineMerge": new_rows,
                "metrics": int(out["指标"].nunique()),
                "countries": sorted(out["国家"].dropna().unique().tolist()) if "国家" in out.columns else ["au"],
                "baseline": str(baseline_path) if baseline_path else None,
                "mode": mode_text,
                "latestByCategory": latest_by_category,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
