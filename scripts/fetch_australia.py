#!/usr/bin/env python3
import argparse
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


def run_mla_exports(raw_dir):
    node = os.environ.get("NODE_BINARY") or "node"
    tool = ROOT / "tools/export_mla_powerbi.cjs"
    outputs = {}
    for name, (url, relative_out) in MLA_REPORTS.items():
        out_path = raw_dir / relative_out
        out_path.parent.mkdir(parents=True, exist_ok=True)
        command = [node, str(tool), "--all-dates", url, str(out_path)]
        print(f"[MLA] exporting {name}: {url}")
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
    value = pd.to_numeric(df[value_col], errors="coerce")
    head = pd.to_numeric(df["Head Count"], errors="coerce")
    avg_head = pd.to_numeric(df["Average Price ($/Head)"], errors="coerce")
    out = pd.DataFrame({"月份": df["月份"], label: value, head_label: head, f"{label}($/Head)": avg_head})
    return out.groupby("月份", as_index=False).agg({label: "mean", head_label: "sum", f"{label}($/Head)": "mean"})


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
    return wide


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
    return (
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


def read_feed_grain_prices(pdfs):
    records = [record for path in pdfs if (record := parse_feed_grain_pdf(path))]
    if not records:
        return pd.DataFrame(columns=["月份"])
    df = pd.DataFrame(records).drop_duplicates("日期").sort_values("日期")
    df["月份"] = month_start(df["日期"])
    value_columns = [label for label in FEED_GRAIN_LABELS.values() if label in df.columns]
    return df.groupby("月份", as_index=False).agg({column: "mean" for column in value_columns}).sort_values("月份")


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
            rows.append(part)
    if not rows:
        raise RuntimeError("No Australia rows were produced")
    out = pd.concat(rows, ignore_index=True)[["品类", "指标", "日期", "数值"]]
    out["日期"] = pd.to_datetime(out["日期"]).dt.strftime("%Y-%m-%d")
    out["数值"] = pd.to_numeric(out["数值"], errors="coerce")
    out = out.dropna(subset=["数值"]).sort_values(["品类", "指标", "日期"])
    return out


def cleanup_previous_outputs(output_dir):
    for path in output_dir.glob("澳洲农业数据_*_合并长表.csv"):
        path.unlink()


def parse_args():
    parser = argparse.ArgumentParser(description="实时抓取澳洲农业公开数据并生成合并长表")
    parser.add_argument("--output-dir", default=str(ROOT / "data"), help="输出 CSV 目录")
    parser.add_argument("--raw-dir", default=str(RAW), help="实时下载源文件暂存目录")
    parser.add_argument(
        "--pork-max-pdfs",
        type=int,
        default=int(os.environ.get("AUSTRALIA_PORK_MAX_PDFS", "0") or "0"),
        help="Australian Pork PDF 下载数量；0 表示下载页面上全部 PDF",
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

    client = session()
    if not args.skip_download:
        run_mla_exports(raw_dir)
        download_abs_tables(client, raw_dir)
        pdfs = download_pork_reports(client, raw_dir, args.pork_max_pdfs)
    else:
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
                "metrics": int(out["指标"].nunique()),
                "latestByCategory": latest_by_category,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
