#!/usr/bin/env python3
import argparse
import csv
import importlib.util
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MOA_BUILD_SCRIPT = ROOT / "scripts/build_moa_workbook.py"
DEFAULT_OUTPUT = ROOT / "dist/dashboard/data/dashboard-data.json"
MAX_POINTS = 260

MOA_INPUT_PATTERNS = [
    "dist/moa/moa_jcyj_data_merged.json",
    "dist/moa/moa_jcyj_data.json",
    "data/moa/baseline_moa_jcyj_data.json",
]

USDA_INPUT_PATTERNS = [
    "data/USDA农业数据_*_合并长表.csv",
    "data/USDA_价格_合并长表.csv",
]


PRODUCTS = [
    {
        "id": "beef",
        "label": "牛肉/活牛",
        "category": "畜肉",
        "chinaMetric": "china_beef",
        "usMetric": "us_cattle",
        "comparability": "方向参考",
        "caveat": "牛肉/活牛已统一为元/kg，但中国偏集贸市场牛肉零售或主产活牛口径，美国为NASS农场端肉牛收到价；适合看趋势和方向，不适合直接解释为终端价差。",
    },
    {
        "id": "hog",
        "label": "生猪",
        "category": "畜肉",
        "chinaMetric": "china_hog",
        "usMetric": "us_hog",
        "comparability": "较可比",
        "caveat": "生猪两边都偏上游价格，统一元/kg后可做较强趋势对照；但中国为周度集贸/监测价格，美国为月度农场端价格，日期和调查体系仍不同。",
    },
    {
        "id": "milk",
        "label": "生鲜乳",
        "category": "乳品",
        "chinaMetric": "china_milk",
        "usMetric": "us_milk",
        "comparability": "较可比",
        "caveat": "生鲜乳商品形态相近，统一元/kg后较可比；但中国为周度主产省份监测，美国为月度NASS调查，频率和样本体系不同。",
    },
    {
        "id": "egg",
        "label": "鸡蛋",
        "category": "禽蛋",
        "chinaMetric": "china_egg",
        "usMetric": "us_egg",
        "comparability": "方向参考",
        "caveat": "鸡蛋已从美元/打按近似重量换算为元/kg，但中美商品规格和采集环节不同；适合趋势对照，不宜做精确价差。",
    },
    {
        "id": "chicken",
        "label": "鸡肉",
        "category": "禽肉",
        "chinaMetric": "china_chicken",
        "usMetric": "us_chicken",
        "comparability": "方向参考",
        "caveat": "鸡肉已统一元/kg，但中国为集贸鸡肉价格，美国为肉鸡农场端收到价，销售环节不同；适合看方向，不适合直接比零售价。",
    },
    {
        "id": "corn",
        "label": "玉米",
        "category": "饲料",
        "chinaMetric": "china_corn",
        "usMetric": "us_corn",
        "comparability": "较可比",
        "caveat": "玉米已统一元/kg，商品形态较接近；但中国为周度集贸/产销区监测，美国为月度农场收到价，仍需注意频率和价格环节差异。",
    },
    {
        "id": "soy",
        "label": "豆粕/大豆",
        "category": "饲料",
        "chinaMetric": "china_soymeal",
        "usMetric": "us_soybeans",
        "comparability": "不可直接比价",
        "caveat": "豆粕/大豆虽然都换算为元/kg，但中国指标是豆粕，美国指标是大豆，不是同一商品；只能观察饲料蛋白链条方向，不能直接比价。",
    },
]


CHINA_METRIC_CONFIGS = [
    ("china_beef", "beef", "牛肉", "牛肉", lambda row: row.get("scope") == "全国"),
    ("china_live_cattle", "beef", "活牛", "活牛", lambda row: row.get("scope") != "全国"),
    ("china_hog", "hog", "生猪", "生猪", lambda row: row.get("scope") == "全国"),
    ("china_pork", "hog", "猪肉", "猪肉", lambda row: row.get("scope") == "全国"),
    ("china_milk", "milk", "生鲜乳", "生鲜乳", lambda row: True),
    ("china_egg", "egg", "鸡蛋", "鸡蛋", lambda row: row.get("scope") == "全国"),
    ("china_chicken", "chicken", "鸡肉", "鸡肉", lambda row: row.get("scope") == "全国"),
    ("china_corn", "corn", "玉米", "玉米", lambda row: row.get("scope") == "全国"),
    ("china_soymeal", "soy", "豆粕", "豆粕", lambda row: row.get("scope") == "全国"),
]


USDA_METRIC_CONFIGS = {
    "牛肉·活牛": ("us_cattle", "beef", "肉牛/活牛"),
    "生猪": ("us_hog", "hog", "生猪"),
    "生鲜乳": ("us_milk", "milk", "生鲜乳"),
    "鸡蛋": ("us_egg", "egg", "鸡蛋"),
    "鸡肉": ("us_chicken", "chicken", "鸡肉"),
    "玉米": ("us_corn", "corn", "玉米"),
    "大豆": ("us_soybeans", "soy", "大豆"),
}


def load_moa_parser():
    spec = importlib.util.spec_from_file_location("moa_build_workbook", MOA_BUILD_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def resolve_path(value):
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    return path


def find_latest(patterns):
    candidates = []
    for pattern in patterns:
        candidates.extend(path for path in ROOT.glob(pattern) if path.is_file())
    if not candidates:
        return None
    return max(candidates, key=lambda path: (path.stat().st_mtime, path.name))


def resolve_input(label, explicit_path, env_name, patterns):
    if explicit_path:
        path = resolve_path(explicit_path)
    elif os.environ.get(env_name):
        path = resolve_path(os.environ[env_name])
    else:
        path = find_latest(patterns)
    if not path or not path.exists():
        raise SystemExit(f"找不到{label}输入文件。请传 --{label} 或设置 {env_name}。")
    return path


def display_path(path):
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def as_float(value):
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def trim_series(series):
    return series[-MAX_POINTS:]


def latest_record(series):
    return series[-1] if series else None


def build_metric(metric_id, country, product_id, label, unit, frequency, source, series, source_note):
    latest = latest_record(series)
    return {
        "id": metric_id,
        "country": country,
        "productId": product_id,
        "label": label,
        "unit": unit,
        "frequency": frequency,
        "source": source,
        "sourceNote": source_note,
        "latest": latest,
        "series": trim_series(series),
    }


def build_china_metrics(moa_json):
    parser = load_moa_parser()
    data = json.loads(moa_json.read_text(encoding="utf-8"))
    market_rows = []
    slaughter_rows = []
    for article in data.get("articles", []):
        market_rows.extend(parser.parse_market_article(article))
        slaughter_rows.extend(parser.parse_slaughter_article(article))

    metrics = []
    for metric_id, product_id, label, item, scope_pred in CHINA_METRIC_CONFIGS:
        by_date = {}
        for row in market_rows:
            if row.get("item") != item or not scope_pred(row):
                continue
            value = as_float(row.get("value"))
            if value is None:
                continue
            by_date[row["publishDate"]] = {
                "date": row["publishDate"],
                "period": row.get("period", ""),
                "value": round(value, 3),
                "momPct": as_float(row.get("momPct")),
                "yoyPct": as_float(row.get("yoyPct")),
                "scope": row.get("scope", ""),
                "url": row.get("url", ""),
            }
        series = [by_date[date] for date in sorted(by_date)]
        metrics.append(
            build_metric(
                metric_id,
                "china",
                product_id,
                label,
                "元/kg",
                "周度",
                "农业农村部畜牧兽医局监测预警",
                series,
                "集贸市场价格；优先取全国口径，活牛/生鲜乳等按可用主产区口径。",
            )
        )

    slaughter_specs = [
        ("china_hog_slaughter", "hog", "生猪屠宰收购价", "生猪平均收购价格"),
        ("china_pork_ex_factory", "hog", "白条肉出厂价", "白条肉平均出厂价格"),
    ]
    for metric_id, product_id, label, item in slaughter_specs:
        by_date = {}
        for row in slaughter_rows:
            if row.get("item") != item:
                continue
            value = as_float(row.get("value"))
            if value is None:
                continue
            by_date[row["publishDate"]] = {
                "date": row["publishDate"],
                "period": row.get("period", ""),
                "value": round(value, 3),
                "momPct": as_float(row.get("momPct")),
                "yoyPct": as_float(row.get("yoyPct")),
                "scope": "全国",
                "url": row.get("url", ""),
            }
        series = [by_date[date] for date in sorted(by_date)]
        metrics.append(
            build_metric(
                metric_id,
                "china",
                product_id,
                label,
                "元/kg",
                "周度",
                "农业农村部生猪定点屠宰价格",
                series,
                "生猪定点屠宰企业价格，适合与集贸生猪/猪肉链条联动观察。",
            )
        )
    return metrics, data


def build_us_metrics(usda_csv):
    rows_by_category = defaultdict(list)
    with usda_csv.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            category = row.get("品类")
            if category in USDA_METRIC_CONFIGS:
                rows_by_category[category].append(row)

    metrics = []
    for category, rows in rows_by_category.items():
        metric_id, product_id, label = USDA_METRIC_CONFIGS[category]
        series = []
        for row in sorted(rows, key=lambda item: item.get("date", "")):
            value = as_float(row.get("价格_元每公斤"))
            if value is None:
                continue
            mom = as_float(row.get("环比"))
            yoy = as_float(row.get("同比"))
            series.append(
                {
                    "date": row.get("date", "")[:10],
                    "period": row.get("date", "")[:7],
                    "value": round(value, 3),
                    "momPct": None if mom is None else round(mom * 100, 3),
                    "yoyPct": None if yoy is None else round(yoy * 100, 3),
                    "scope": "美国全国",
                    "originalValue": as_float(row.get("价格_美制")),
                    "originalUnit": row.get("美制单位", ""),
                }
            )
        metrics.append(
            build_metric(
                metric_id,
                "us",
                product_id,
                label,
                "元/kg",
                "月度",
                "USDA NASS Quick Stats",
                series,
                "美国农场端价格收到价；按当前汇率与单位换算为元/kg，趋势优先于绝对可比。",
            )
        )
    return metrics


def build_comparisons(metrics_by_id):
    comparisons = []
    for product in PRODUCTS:
        china = metrics_by_id.get(product["chinaMetric"])
        us = metrics_by_id.get(product["usMetric"])
        if not china or not us or not china.get("latest") or not us.get("latest"):
            continue
        china_value = china["latest"]["value"]
        us_value = us["latest"]["value"]
        comparisons.append(
            {
                "productId": product["id"],
                "label": product["label"],
                "category": product["category"],
                "chinaMetric": china["id"],
                "usMetric": us["id"],
                "chinaDate": china["latest"]["date"],
                "usDate": us["latest"]["date"],
                "chinaValue": china_value,
                "usValue": us_value,
                "gap": round(china_value - us_value, 3),
                "ratio": None if us_value == 0 else round(china_value / us_value, 3),
                "comparability": product["comparability"],
                "caveat": product["caveat"],
            }
        )
    return comparisons


def parse_args():
    parser = argparse.ArgumentParser(description="生成中美农业数据看板的数据文件")
    parser.add_argument("--moa-json", help="农业部本周合并后的 JSON；不传则自动找最新可用文件")
    parser.add_argument("--usda-csv", help="USDA 本周合并长表 CSV；不传则自动找最新可用文件")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="输出 dashboard-data.json")
    return parser.parse_args()


def main():
    args = parse_args()
    moa_json = resolve_input("moa-json", args.moa_json, "MOA_DASHBOARD_JSON", MOA_INPUT_PATTERNS)
    usda_csv = resolve_input("usda-csv", args.usda_csv, "USDA_DASHBOARD_CSV", USDA_INPUT_PATTERNS)
    output = resolve_path(args.output)

    china_metrics, moa_data = build_china_metrics(moa_json)
    us_metrics = build_us_metrics(usda_csv)
    metrics = china_metrics + us_metrics
    metrics_by_id = {metric["id"]: metric for metric in metrics}
    comparisons = build_comparisons(metrics_by_id)
    latest_china = max((m["latest"]["date"] for m in china_metrics if m.get("latest")), default="")
    latest_us = max((m["latest"]["date"] for m in us_metrics if m.get("latest")), default="")
    payload = {
        "meta": {
            "title": "中美农业数据监测看板",
            "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "latestChinaDate": latest_china,
            "latestUsDate": latest_us,
            "sourceFiles": {
                "china": display_path(moa_json),
                "us": display_path(usda_csv),
            },
            "notes": [
                "单位说明：看板已把中美价格统一换算为元/kg，这解决的是计量单位问题，不代表价格口径完全一致。",
                "环节差异：中国农业部多为周度集贸市场、主产省份或产销区监测价格；USDA NASS多为月度农场端Price Received，中间可能相差屠宰、分割、批发、零售等环节。",
                "商品差异：生猪、生鲜乳、玉米商品形态较接近；牛肉/活牛、鸡蛋、鸡肉更多适合方向参考；豆粕/大豆不是同一商品，不可直接比价。",
                "标签说明：较可比=可做较强趋势和水平参考；方向参考=主要看趋势方向和异常变化；不可直接比价=只看产业链方向，不解释绝对价差。",
                "频率说明：中国数据通常为周度，美国数据通常为月度，最新值日期不一定完全同步。",
            ],
        },
        "countries": {
            "china": {
                "label": "中国",
                "shortLabel": "中国农业部",
                "frequency": "周度",
                "latestDate": latest_china,
                "source": "农业农村部畜牧兽医局监测预警",
                "articleCount": moa_data.get("articleCount"),
            },
            "us": {
                "label": "美国",
                "shortLabel": "USDA NASS",
                "frequency": "月度",
                "latestDate": latest_us,
                "source": "USDA NASS Quick Stats",
                "articleCount": None,
            },
        },
        "products": PRODUCTS,
        "metrics": metrics,
        "comparisons": comparisons,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() == ".js":
        text = "window.CHINA_US_AGRI_DASHBOARD_DATA = "
        text += json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        text += ";\n"
    else:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        text += "\n"
    output.write_text(text, encoding="utf-8")
    print(json.dumps({
        "output": str(output),
        "moaJson": display_path(moa_json),
        "usdaCsv": display_path(usda_csv),
        "metrics": len(metrics),
        "comparisons": len(comparisons),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
