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
    "data/美国USDA农业数据_*_合并长表.csv",
    "data/USDA农业数据_*_合并长表.csv",
    "data/USDA_价格_合并长表.csv",
]

AUSTRALIA_INPUT_PATTERNS = [
    "data/澳洲农业数据_*_合并长表.csv",
    "data/australia_agri_*_long.csv",
]

COUNTRY_ORDER = ["china", "us", "au"]
COUNTRY_LABELS = {
    "china": "中国",
    "us": "美国",
    "au": "澳洲",
}
AUD_CNY = 4.65


PRODUCTS = [
    {
        "id": "beef",
        "label": "牛肉/活牛",
        "category": "畜肉",
        "chinaMetric": "china_beef",
        "usMetric": "us_cattle",
        "countryMetrics": {"china": "china_beef", "us": "us_cattle", "au": "au_eyci"},
        "comparisonMetrics": {"china": "china_live_cattle", "us": "us_cattle", "au": "au_nyci"},
        "comparability": "方向参考",
        "caveat": "各国对比采用更接近活牛/活重的口径：中国活牛、美国NASS肉牛、澳洲NYCI；澳洲EYCI、成交头数、屠宰量和牛肉产量保留在澳洲单国视图中，不混入口径不同的价格对比。",
    },
    {
        "id": "hog",
        "label": "生猪",
        "category": "畜肉",
        "chinaMetric": "china_hog",
        "usMetric": "us_hog",
        "countryMetrics": {"china": "china_hog", "us": "us_hog", "au": "au_hog_avg"},
        "comparisonMetrics": {"china": "china_hog", "us": "us_hog", "au": "au_hog_avg"},
        "comparability": "较可比",
        "caveat": "各国对比使用中国生猪、美国NASS生猪和澳洲Buyers NAT平均价的统一换算值；澳洲最高价、屠宰量和猪肉产量仅在澳洲单国视图保留原始口径。",
    },
    {
        "id": "milk",
        "label": "生鲜乳",
        "category": "乳品",
        "chinaMetric": "china_milk",
        "usMetric": "us_milk",
        "countryMetrics": {"china": "china_milk", "us": "us_milk", "au": "au_milk_cpi"},
        "comparisonMetrics": {"china": "china_milk", "us": "us_milk"},
        "comparability": "较可比",
        "caveat": "各国对比只展示中美生鲜乳价格；澳洲当前为ABS Milk CPI指数，不是农场价或金额价格，因此只在澳洲单国视图展示。",
    },
    {
        "id": "egg",
        "label": "鸡蛋",
        "category": "禽蛋",
        "chinaMetric": "china_egg",
        "usMetric": "us_egg",
        "countryMetrics": {"china": "china_egg", "us": "us_egg", "au": "au_egg_cpi"},
        "comparisonMetrics": {"china": "china_egg", "us": "us_egg"},
        "comparability": "方向参考",
        "caveat": "各国对比只展示中美鸡蛋价格；澳洲当前为ABS Eggs CPI指数，不能与价格金额直接比较。",
    },
    {
        "id": "chicken",
        "label": "鸡肉",
        "category": "禽肉",
        "chinaMetric": "china_chicken",
        "usMetric": "us_chicken",
        "countryMetrics": {"china": "china_chicken", "us": "us_chicken", "au": "au_chicken_cpi"},
        "comparisonMetrics": {"china": "china_chicken", "us": "us_chicken"},
        "comparability": "方向参考",
        "caveat": "各国对比只展示中美鸡肉价格；澳洲当前为ABS Poultry CPI指数，屠宰量和鸡肉产量只在澳洲单国视图展示。",
    },
    {
        "id": "corn",
        "label": "玉米",
        "category": "饲料",
        "chinaMetric": "china_corn",
        "usMetric": "us_corn",
        "countryMetrics": {"china": "china_corn", "us": "us_corn"},
        "comparisonMetrics": {"china": "china_corn", "us": "us_corn"},
        "comparability": "较可比",
        "caveat": "玉米已统一元/kg，商品形态较接近；但中国为周度集贸/产销区监测，美国为月度农场收到价，仍需注意频率和价格环节差异。",
    },
    {
        "id": "soy",
        "label": "豆粕/大豆",
        "category": "饲料",
        "chinaMetric": "china_soymeal",
        "usMetric": "us_soybeans",
        "countryMetrics": {"china": "china_soymeal", "us": "us_soybeans", "au": "au_soymeal"},
        "comparisonMetrics": {"china": "china_soymeal", "au": "au_soymeal"},
        "comparability": "较可比",
        "caveat": "各国对比只展示中国豆粕和澳洲豆粕；美国指标是大豆，不是同一商品，因此保留在美国单国视图，不进入豆粕比价。",
    },
    {
        "id": "feed",
        "label": "饲料",
        "category": "饲料",
        "countryMetrics": {"au": "au_feed_wheat"},
        "comparability": "澳洲单国",
        "caveat": "澳洲饲料页集中展示豆粕、饲料小麦、饲料大麦、高粱、油菜粕、棉籽、小黑麦和饲料燕麦等Australian Pork/ProFarmer周报Delivered报价月均；当前中美看板无完全同组指标，作为澳洲单国趋势观察。",
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

AUSTRALIA_METRIC_CONFIGS = {
    "EYCI月均(c/kg cwt)": {
        "id": "au_eyci",
        "productId": "beef",
        "label": "EYCI",
        "unit": "A¢/kg cwt",
        "frequency": "月度",
        "source": "MLA/NLRS",
        "scope": "澳洲东部",
        "note": "MLA Eastern Young Cattle Indicator，胴体重口径，单国视图保留原始A¢/kg cwt。",
        "compare": "aud_cents_kg_to_rmb_kg",
        "compareUnit": "元/kg",
    },
    "EYCI月均(c/kg cwt)($/Head)": {
        "id": "au_eyci_head",
        "productId": "beef",
        "label": "EYCI折算头均价值",
        "unit": "A$/head",
        "frequency": "月度",
        "source": "MLA/NLRS",
        "scope": "澳洲东部",
        "note": "MLA EYCI按头折算价值，适合观察头均价值变化，不进入跨国价格对比。",
        "compare": None,
    },
    "EYCI月成交头数": {
        "id": "au_eyci_head_count",
        "productId": "beef",
        "label": "EYCI成交头数",
        "unit": "头",
        "frequency": "月度",
        "source": "MLA/NLRS",
        "scope": "澳洲东部",
        "note": "MLA EYCI样本成交头数，反映样本市场活跃度，不进入跨国价格对比。",
        "compare": None,
    },
    "Feeder Steer月均(c/kg lwt)": {
        "id": "au_feeder_steer",
        "productId": "beef",
        "label": "Feeder Steer",
        "unit": "A¢/kg lwt",
        "frequency": "月度",
        "source": "MLA/NLRS",
        "scope": "澳洲",
        "note": "MLA Feeder Steer活重价格，单国视图保留原始A¢/kg lwt。",
        "compare": "aud_cents_kg_to_rmb_kg",
        "compareUnit": "元/kg",
    },
    "Heavy Steer月均(c/kg lwt)": {
        "id": "au_heavy_steer",
        "productId": "beef",
        "label": "Heavy Steer",
        "unit": "A¢/kg lwt",
        "frequency": "月度",
        "source": "MLA/NLRS",
        "scope": "澳洲",
        "note": "MLA Heavy Steer活重价格，单国视图保留原始A¢/kg lwt。",
        "compare": "aud_cents_kg_to_rmb_kg",
        "compareUnit": "元/kg",
    },
    "NYCI月均(c/kg lwt)": {
        "id": "au_nyci",
        "productId": "beef",
        "label": "NYCI",
        "unit": "A¢/kg lwt",
        "frequency": "月度",
        "source": "MLA/NLRS",
        "scope": "澳洲全国",
        "note": "MLA National Young Cattle Indicator，活重口径；各国活牛/肉牛对比优先使用该指标的元/kg换算值。",
        "compare": "aud_cents_kg_to_rmb_kg",
        "compareUnit": "元/kg",
    },
    "NYCI月均(c/kg lwt)($/Head)": {
        "id": "au_nyci_head",
        "productId": "beef",
        "label": "NYCI折算头均价值",
        "unit": "A$/head",
        "frequency": "月度",
        "source": "MLA/NLRS",
        "scope": "澳洲全国",
        "note": "MLA NYCI按头折算价值，适合观察头均价值变化，不进入跨国价格对比。",
        "compare": None,
    },
    "NYCI月成交头数": {
        "id": "au_nyci_head_count",
        "productId": "beef",
        "label": "NYCI成交头数",
        "unit": "头",
        "frequency": "月度",
        "source": "MLA/NLRS",
        "scope": "澳洲全国",
        "note": "MLA NYCI样本成交头数，反映样本市场活跃度，不进入跨国价格对比。",
        "compare": None,
    },
    "牛屠宰量(千头,季度)": {
        "id": "au_cattle_slaughter",
        "productId": "beef",
        "label": "牛屠宰量",
        "unit": "千头",
        "frequency": "季度",
        "source": "ABS Livestock Products",
        "scope": "澳洲全国",
        "note": "ABS季度牛屠宰量，数量口径，不进入价格对比。",
        "compare": None,
    },
    "牛肉产量(吨,季度)": {
        "id": "au_beef_production",
        "productId": "beef",
        "label": "牛肉产量",
        "unit": "吨",
        "frequency": "季度",
        "source": "ABS Livestock Products",
        "scope": "澳洲全国",
        "note": "ABS季度牛肉产量，产量口径，不进入价格对比。",
        "compare": None,
    },
    "牛肉小牛肉CPI指数": {
        "id": "au_beef_cpi",
        "productId": "beef",
        "label": "牛肉小牛肉CPI指数",
        "unit": "指数",
        "frequency": "月度",
        "source": "ABS CPI",
        "scope": "澳洲全国",
        "note": "ABS Beef and veal CPI，指数基期为2025-09=100，不与价格金额直接比水平。",
        "compare": None,
    },
    "猪价 Buyers NAT 60.1-75kg平均(c/kg HSCW)": {
        "id": "au_hog_avg",
        "productId": "hog",
        "label": "猪价 Buyers NAT",
        "unit": "A¢/kg HSCW",
        "frequency": "月度",
        "source": "Australian Pork Eyes & Ears",
        "scope": "澳洲全国",
        "note": "Australian Pork周报Buyers NAT 60.1-75kg平均价，单国视图保留原始A¢/kg HSCW。",
        "compare": "aud_cents_kg_to_rmb_kg",
        "compareUnit": "元/kg",
    },
    "猪价 Buyers NAT 60.1-75kg最高(c/kg HSCW)": {
        "id": "au_hog_high",
        "productId": "hog",
        "label": "猪价 Buyers NAT最高",
        "unit": "A¢/kg HSCW",
        "frequency": "月度",
        "source": "Australian Pork Eyes & Ears",
        "scope": "澳洲全国",
        "note": "Australian Pork周报Buyers NAT 60.1-75kg最高价，单国视图保留原始A¢/kg HSCW。",
        "compare": "aud_cents_kg_to_rmb_kg",
        "compareUnit": "元/kg",
    },
    "猪屠宰量(千头,季度)": {
        "id": "au_hog_slaughter",
        "productId": "hog",
        "label": "猪屠宰量",
        "unit": "千头",
        "frequency": "季度",
        "source": "ABS Livestock Products",
        "scope": "澳洲全国",
        "note": "ABS季度猪屠宰量，数量口径，不进入价格对比。",
        "compare": None,
    },
    "猪肉产量(吨,季度)": {
        "id": "au_pork_production",
        "productId": "hog",
        "label": "猪肉产量",
        "unit": "吨",
        "frequency": "季度",
        "source": "ABS Livestock Products",
        "scope": "澳洲全国",
        "note": "ABS季度猪肉产量，产量口径，不进入价格对比。",
        "compare": None,
    },
    "猪肉CPI指数": {
        "id": "au_pork_cpi",
        "productId": "hog",
        "label": "猪肉CPI指数",
        "unit": "指数",
        "frequency": "月度",
        "source": "ABS CPI",
        "scope": "澳洲全国",
        "note": "ABS Pork CPI，指数基期为2025-09=100，不与价格金额直接比水平。",
        "compare": None,
    },
    "牛奶CPI指数": {
        "id": "au_milk_cpi",
        "productId": "milk",
        "label": "牛奶CPI指数",
        "unit": "指数",
        "frequency": "月度",
        "source": "ABS CPI",
        "scope": "澳洲全国",
        "note": "ABS Milk CPI，指数基期为2025-09=100；不是农场价或零售价金额，不与元/kg价格直接比水平。",
        "compare": None,
    },
    "鸡蛋CPI指数": {
        "id": "au_egg_cpi",
        "productId": "egg",
        "label": "鸡蛋CPI指数",
        "unit": "指数",
        "frequency": "月度",
        "source": "ABS CPI",
        "scope": "澳洲全国",
        "note": "ABS Eggs CPI，指数基期为2025-09=100；不是价格金额，不与元/kg价格直接比水平。",
        "compare": None,
    },
    "禽肉CPI指数": {
        "id": "au_chicken_cpi",
        "productId": "chicken",
        "label": "禽肉CPI指数",
        "unit": "指数",
        "frequency": "月度",
        "source": "ABS CPI",
        "scope": "澳洲全国",
        "note": "ABS Poultry CPI，指数基期为2025-09=100；不是价格金额，不与元/kg价格直接比水平。",
        "compare": None,
    },
    "鸡屠宰量(千头,季度)": {
        "id": "au_chicken_slaughter",
        "productId": "chicken",
        "label": "鸡屠宰量",
        "unit": "千头",
        "frequency": "季度",
        "source": "ABS Livestock Products",
        "scope": "澳洲全国",
        "note": "ABS季度鸡屠宰量，数量口径，不进入价格对比。",
        "compare": None,
    },
    "鸡肉产量(吨,季度)": {
        "id": "au_chicken_production",
        "productId": "chicken",
        "label": "鸡肉产量",
        "unit": "吨",
        "frequency": "季度",
        "source": "ABS Livestock Products",
        "scope": "澳洲全国",
        "note": "ABS季度鸡肉产量，产量口径，不进入价格对比。",
        "compare": None,
    },
    "豆粕澳洲交付价月均(A$/t)": {
        "id": "au_soymeal",
        "productId": "feed",
        "label": "豆粕",
        "unit": "A$/t",
        "frequency": "月度",
        "source": "Australian Pork/ProFarmer",
        "scope": "澳洲Delivered区域均值",
        "note": "ProFarmer Weekly Grain Table中的澳洲Delivered Soy meal周价，按地区TW均值再月均；单国视图保留原始A$/t。",
        "compare": "aud_t_to_rmb_kg",
        "compareUnit": "元/kg",
    },
    "饲料小麦澳洲交付价月均(A$/t)": {
        "id": "au_feed_wheat",
        "productId": "feed",
        "label": "饲料小麦",
        "unit": "A$/t",
        "frequency": "月度",
        "source": "Australian Pork/ProFarmer",
        "scope": "澳洲Delivered区域均值",
        "note": "ProFarmer Weekly Grain Table中的澳洲Delivered Feed Wheat周价，按地区TW均值再月均；单国视图保留原始A$/t。",
        "compare": "aud_t_to_rmb_kg",
        "compareUnit": "元/kg",
    },
    "饲料大麦澳洲交付价月均(A$/t)": {
        "id": "au_feed_barley",
        "productId": "feed",
        "label": "饲料大麦",
        "unit": "A$/t",
        "frequency": "月度",
        "source": "Australian Pork/ProFarmer",
        "scope": "澳洲Delivered区域均值",
        "note": "ProFarmer Weekly Grain Table中的澳洲Delivered Feed Barley周价，按地区TW均值再月均；单国视图保留原始A$/t。",
        "compare": "aud_t_to_rmb_kg",
        "compareUnit": "元/kg",
    },
    "高粱澳洲交付价月均(A$/t)": {
        "id": "au_sorghum",
        "productId": "feed",
        "label": "高粱",
        "unit": "A$/t",
        "frequency": "月度",
        "source": "Australian Pork/ProFarmer",
        "scope": "澳洲Delivered区域均值",
        "note": "ProFarmer Weekly Grain Table中的澳洲Delivered Sorghum周价，按地区TW均值再月均；单国视图保留原始A$/t。",
        "compare": "aud_t_to_rmb_kg",
        "compareUnit": "元/kg",
    },
    "油菜粕澳洲交付价月均(A$/t)": {
        "id": "au_canola_meal",
        "productId": "feed",
        "label": "油菜粕",
        "unit": "A$/t",
        "frequency": "月度",
        "source": "Australian Pork/ProFarmer",
        "scope": "澳洲Delivered区域均值",
        "note": "ProFarmer Weekly Grain Table中的澳洲Delivered Canola meal周价，按地区TW均值再月均；单国视图保留原始A$/t。",
        "compare": "aud_t_to_rmb_kg",
        "compareUnit": "元/kg",
    },
    "棉籽澳洲交付价月均(A$/t)": {
        "id": "au_cottonseed",
        "productId": "feed",
        "label": "棉籽",
        "unit": "A$/t",
        "frequency": "月度",
        "source": "Australian Pork/ProFarmer",
        "scope": "澳洲Delivered区域均值",
        "note": "ProFarmer Weekly Grain Table中的澳洲Delivered Cottonseed周价，按地区TW均值再月均；单国视图保留原始A$/t。",
        "compare": "aud_t_to_rmb_kg",
        "compareUnit": "元/kg",
    },
    "小黑麦澳洲交付价月均(A$/t)": {
        "id": "au_triticale",
        "productId": "feed",
        "label": "小黑麦",
        "unit": "A$/t",
        "frequency": "月度",
        "source": "Australian Pork/ProFarmer",
        "scope": "澳洲Delivered区域均值",
        "note": "ProFarmer Weekly Grain Table中的澳洲Delivered Triticale周价，按地区TW均值再月均；单国视图保留原始A$/t。",
        "compare": "aud_t_to_rmb_kg",
        "compareUnit": "元/kg",
    },
    "饲料燕麦澳洲交付价月均(A$/t)": {
        "id": "au_feed_oats",
        "productId": "feed",
        "label": "饲料燕麦",
        "unit": "A$/t",
        "frequency": "月度",
        "source": "Australian Pork/ProFarmer",
        "scope": "澳洲Delivered区域均值",
        "note": "ProFarmer Weekly Grain Table中的澳洲Delivered Feed Oats周价，按地区TW均值再月均；单国视图保留原始A$/t。",
        "compare": "aud_t_to_rmb_kg",
        "compareUnit": "元/kg",
    },
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


def build_metric(metric_id, country, product_id, label, unit, frequency, source, series, source_note, compare_unit=None):
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
        "compareUnit": compare_unit,
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
                "compareValue": round(value, 3),
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
                "元/kg",
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
                "compareValue": round(value, 3),
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
                "元/kg",
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
        display_unit = ""
        for row in sorted(rows, key=lambda item: item.get("date", "")):
            raw_value = as_float(row.get("价格_美制"))
            compare_value = as_float(row.get("价格_元每公斤"))
            value = raw_value if raw_value is not None else compare_value
            if value is None:
                continue
            display_unit = display_unit or row.get("美制单位", "")
            mom = as_float(row.get("环比"))
            yoy = as_float(row.get("同比"))
            series.append(
                {
                    "date": row.get("date", "")[:10],
                    "period": row.get("date", "")[:7],
                    "value": round(value, 3),
                    "compareValue": None if compare_value is None else round(compare_value, 3),
                    "momPct": None if mom is None else round(mom * 100, 3),
                    "yoyPct": None if yoy is None else round(yoy * 100, 3),
                    "scope": "美国全国",
                    "originalValue": raw_value,
                    "originalUnit": row.get("美制单位", ""),
                }
            )
        metrics.append(
            build_metric(
                metric_id,
                "us",
                product_id,
                label,
                display_unit or "美元原始单位",
                "月度",
                "USDA NASS Quick Stats",
                series,
                "美国农场端Price Received；单国视图保留USDA原始美元单位，各国对比使用数据文件中的元/kg换算值。",
                "元/kg",
            )
        )
    return metrics


def convert_australia_value(value, convert_kind):
    if value is None:
        return None
    if not convert_kind:
        return None
    if convert_kind == "aud_cents_kg_to_rmb_kg":
        return value * AUD_CNY / 100
    if convert_kind == "aud_t_to_rmb_kg":
        return value * AUD_CNY / 1000
    return None


def add_change_fields(series):
    for idx, row in enumerate(series):
        prev = series[idx - 1]["value"] if idx > 0 else None
        prev_year = series[idx - 12]["value"] if idx >= 12 else None
        row["momPct"] = None if not prev else round((row["value"] / prev - 1) * 100, 3)
        row["yoyPct"] = None if not prev_year else round((row["value"] / prev_year - 1) * 100, 3)
    return series


def build_australia_metrics(australia_csv):
    rows_by_metric = defaultdict(list)
    with australia_csv.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            metric_name = row.get("指标")
            if metric_name in AUSTRALIA_METRIC_CONFIGS:
                rows_by_metric[metric_name].append(row)

    metrics = []
    for metric_name, config in AUSTRALIA_METRIC_CONFIGS.items():
        rows = rows_by_metric.get(metric_name, [])
        series = []
        for row in sorted(rows, key=lambda item: item.get("日期", "")):
            raw_value = as_float(row.get("数值"))
            if raw_value is None:
                continue
            compare_value = convert_australia_value(raw_value, config.get("compare"))
            series.append(
                {
                    "date": row.get("日期", "")[:10],
                    "period": row.get("日期", "")[:7],
                    "value": round(raw_value, 3),
                    "compareValue": None if compare_value is None else round(compare_value, 3),
                    "momPct": None,
                    "yoyPct": None,
                    "scope": config["scope"],
                    "originalValue": raw_value,
                    "originalUnit": config["unit"],
                }
            )
        series = add_change_fields(series)
        metrics.append(
            build_metric(
                config["id"],
                "au",
                config["productId"],
                config["label"],
                config["unit"],
                config["frequency"],
                config["source"],
                series,
                config["note"],
                config.get("compareUnit"),
            )
        )
    return metrics


def country_metric_id(product, country):
    if product.get("countryMetrics"):
        return product["countryMetrics"].get(country)
    if country == "china":
        return product.get("chinaMetric")
    if country == "us":
        return product.get("usMetric")
    return product.get(f"{country}Metric")


def comparison_metric_id(product, country):
    if not product.get("comparisonMetrics"):
        return None
    return product["comparisonMetrics"].get(country)


def latest_compare_record(metric):
    rows = [row for row in metric.get("series", []) if row.get("compareValue") is not None]
    return rows[-1] if rows else None


def build_comparisons(metrics_by_id):
    comparisons = []
    for product in PRODUCTS:
        country_values = {}
        for country in COUNTRY_ORDER:
            metric_id = comparison_metric_id(product, country)
            metric = metrics_by_id.get(metric_id) if metric_id else None
            row = latest_compare_record(metric) if metric else None
            if not metric or not row:
                continue
            unit = metric.get("compareUnit")
            if not unit:
                continue
            country_values[country] = {
                "metric": metric["id"],
                "label": metric["label"],
                "date": row["date"],
                "value": row["compareValue"],
                "unit": unit,
                "displayValue": row["value"],
                "displayUnit": metric["unit"],
            }
        if len(country_values) < 2:
            continue
        units = {item["unit"] for item in country_values.values()}
        same_unit = len(units) == 1
        if not same_unit:
            continue
        numeric_values = [item["value"] for item in country_values.values() if item.get("value") is not None]
        min_value = min(numeric_values) if same_unit and numeric_values else None
        max_value = max(numeric_values) if same_unit and numeric_values else None
        ratio = None if not min_value else round(max_value / min_value, 3)
        gap = None if min_value is None or max_value is None else round(max_value - min_value, 3)
        china = country_values.get("china")
        us = country_values.get("us")
        comparisons.append(
            {
                "productId": product["id"],
                "label": product["label"],
                "category": product["category"],
                "countryValues": country_values,
                "countries": list(country_values.keys()),
                "sameUnit": same_unit,
                "unit": next(iter(units)) if same_unit and units else "",
                "gap": gap,
                "ratio": ratio,
                "chinaMetric": china["metric"] if china else None,
                "usMetric": us["metric"] if us else None,
                "chinaDate": china["date"] if china else "",
                "usDate": us["date"] if us else "",
                "chinaValue": china["value"] if china else None,
                "usValue": us["value"] if us else None,
                "comparability": product["comparability"],
                "caveat": product["caveat"],
            }
        )
    return comparisons


def parse_args():
    parser = argparse.ArgumentParser(description="生成各国农业数据看板的数据文件")
    parser.add_argument("--moa-json", help="农业部本周合并后的 JSON；不传则自动找最新可用文件")
    parser.add_argument("--usda-csv", help="USDA 本周合并长表 CSV；不传则自动找最新可用文件")
    parser.add_argument("--australia-csv", help="澳洲农业数据合并长表 CSV；不传则自动找最新可用文件")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="输出 dashboard-data.json")
    return parser.parse_args()


def main():
    args = parse_args()
    moa_json = resolve_input("moa-json", args.moa_json, "MOA_DASHBOARD_JSON", MOA_INPUT_PATTERNS)
    usda_csv = resolve_input("usda-csv", args.usda_csv, "USDA_DASHBOARD_CSV", USDA_INPUT_PATTERNS)
    australia_csv = resolve_input("australia-csv", args.australia_csv, "AUSTRALIA_DASHBOARD_CSV", AUSTRALIA_INPUT_PATTERNS)
    output = resolve_path(args.output)

    china_metrics, moa_data = build_china_metrics(moa_json)
    us_metrics = build_us_metrics(usda_csv)
    au_metrics = build_australia_metrics(australia_csv)
    metrics = china_metrics + us_metrics + au_metrics
    metrics_by_id = {metric["id"]: metric for metric in metrics}
    comparisons = build_comparisons(metrics_by_id)
    latest_china = max((m["latest"]["date"] for m in china_metrics if m.get("latest")), default="")
    latest_us = max((m["latest"]["date"] for m in us_metrics if m.get("latest")), default="")
    latest_au = max((m["latest"]["date"] for m in au_metrics if m.get("latest")), default="")
    payload = {
        "meta": {
            "title": "各国农业数据监测看板",
            "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "latestChinaDate": latest_china,
            "latestUsDate": latest_us,
            "latestAustraliaDate": latest_au,
            "countryOrder": COUNTRY_ORDER,
            "sourceFiles": {
                "china": display_path(moa_json),
                "us": display_path(usda_csv),
                "au": display_path(australia_csv),
            },
            "notes": [
                "显示规则：除“各国对比”外，中国、美国、澳洲单国看板均展示各自数据源的原始口径和原始单位，例如美元/美制单位、澳元/澳分、指数、头数或吨。",
                "对比规则：“各国对比”只展示同商品、同价格口径可进入比较的国家，并使用统一换算后的元/kg；不能比较的国家不会进入该品类的对比图和对比卡片。",
                "澳洲说明：澳洲NYCI、EYCI、成交头数、屠宰量、产量、CPI和饲料报价均纳入澳洲单国视图；其中CPI、成交头数、屠宰量和产量不进入价格对比。",
                "环节差异：中国农业部多为周度集贸市场、主产省份或产销区监测价格；USDA NASS多为月度农场端Price Received；澳洲来自MLA、Australian Pork/ProFarmer和ABS CPI，环节差异需单独看口径提示。",
                "商品差异：牛使用中国活牛、美国肉牛、澳洲NYCI做方向参考；生猪使用三国上游/胴体相关价格；奶、蛋、鸡肉当前只比较中美；豆粕只比较中国和澳洲。",
                "标签说明：较可比=可做较强趋势和水平参考；方向参考=主要看趋势方向和异常变化；不可直接比价=只看产业链方向，不解释绝对价差。",
                "频率说明：中国数据通常为周度，美国和澳洲数据多为月度，最新值日期不一定完全同步。",
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
            "au": {
                "label": "澳洲",
                "shortLabel": "MLA / ABS / APL",
                "frequency": "月度",
                "latestDate": latest_au,
                "source": "MLA/NLRS、Australian Pork/ProFarmer、ABS CPI",
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
        "australiaCsv": display_path(australia_csv),
        "metrics": len(metrics),
        "comparisons": len(comparisons),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
