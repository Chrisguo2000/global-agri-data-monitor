#!/usr/bin/env python3
"""
USDA NASS Quick Stats 自动抓取脚本
- 拉取 7 个品类的"价格收到价(Price Received)"全国月度历史
- 保存原始 CSV(存档) + 生成带 元/公斤换算、环比、同比 的整理表(xlsx/csv)
- key 从环境变量 NASS_API_KEY 读取(GitHub Secret),不写死在代码里

本地测试:  NASS_API_KEY=你的key  python fetch_usda.py
"""
import os, io, sys, datetime as dt
import requests
import pandas as pd

KEY = os.environ.get("NASS_API_KEY")
if not KEY:
    sys.exit("缺少环境变量 NASS_API_KEY")

# 美元兑人民币,用于"元/公斤"换算列;可用环境变量覆盖
FX_USD_CNY = float(os.environ.get("FX_USD_CNY", "6.77"))

BASE = "https://quickstats.nass.usda.gov/api/api_GET/"
OUT_RAW = "data/raw"
OUT_PROC = "data"
os.makedirs(OUT_RAW, exist_ok=True)
os.makedirs(OUT_PROC, exist_ok=True)

# 品类配置: 单位 / 每单位公斤数 / 主指标 short_desc / 中文名
CATS = {
    "cattle": dict(commodity="CATTLE", unit="$ / CWT", kg=45.3592,
        cn="牛肉·活牛",
        head="CATTLE, GE 500 LBS - PRICE RECEIVED, MEASURED IN $ / CWT"),
    "hogs": dict(commodity="HOGS", unit="$ / CWT", kg=45.3592,
        cn="生猪",
        head="HOGS - PRICE RECEIVED, MEASURED IN $ / CWT"),
    "milk": dict(commodity="MILK", unit="$ / CWT", kg=45.3592,
        cn="生鲜乳",
        head="MILK - PRICE RECEIVED, MEASURED IN $ / CWT"),
    "eggs": dict(commodity="EGGS", unit="$ / DOZEN", kg=0.68,
        cn="鸡蛋",
        head="EGGS - PRICE RECEIVED, MEASURED IN $ / DOZEN"),
    "chickens": dict(commodity="CHICKENS", unit="$ / LB", kg=0.453592,
        cn="鸡肉",
        head="CHICKENS, BROILERS - PRICE RECEIVED, MEASURED IN $ / LB"),
    "corn": dict(commodity="CORN", unit="$ / BU", kg=25.4012,
        cn="玉米",
        head="CORN, GRAIN - PRICE RECEIVED, MEASURED IN $ / BU"),
    "soybeans": dict(commodity="SOYBEANS", unit="$ / BU", kg=27.2155,
        cn="大豆",
        head="SOYBEANS - PRICE RECEIVED, MEASURED IN $ / BU"),
}


def fetch(commodity: str) -> pd.DataFrame:
    """调用 NASS API,返回原始 DataFrame(与手动下载完全同源)"""
    params = {
        "key": KEY,
        "source_desc": "SURVEY",
        "commodity_desc": commodity,
        "statisticcat_desc": "PRICE RECEIVED",
        "agg_level_desc": "NATIONAL",
        "format": "CSV",
    }
    r = requests.get(BASE, params=params, timeout=180)
    if r.status_code != 200:
        # NASS 在记录数>5万或参数错时会返回错误信息正文
        raise RuntimeError(f"{commodity} 请求失败 HTTP {r.status_code}: {r.text[:300]}")
    return pd.read_csv(io.StringIO(r.text), low_memory=False)


def process(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """筛月度+主单位,算 元/公斤 / 环比 / 同比"""
    d = df[(df.freq_desc == "MONTHLY") &
           (df.unit_desc == cfg["unit"]) &
           (df.agg_level_desc == "NATIONAL") &
           (df.short_desc == cfg["head"])].copy()
    d["Value"] = pd.to_numeric(d["Value"].astype(str).str.replace(",", ""), errors="coerce")
    d["date"] = d.apply(lambda x: dt.date(int(x["year"]), int(x["begin_code"]), 1), axis=1)
    d = d.dropna(subset=["Value"]).sort_values("date")
    d = d[["date", "year", "Value"]].rename(columns={"Value": "价格_美制"})
    d["价格_元每公斤"] = (d["价格_美制"] * FX_USD_CNY / cfg["kg"]).round(3)
    d["环比"] = d["价格_美制"].pct_change().round(4)            # 与上月比
    d["同比"] = d["价格_美制"].pct_change(12).round(4)          # 与去年同月比(月度连续时)
    d.insert(0, "品类", cfg["cn"])
    d.insert(2, "美制单位", cfg["unit"])
    return d


def main():
    stamp = dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    all_proc = []
    with pd.ExcelWriter(f"{OUT_PROC}/USDA畜牧饲料价格_自动更新.xlsx", engine="openpyxl") as xw:
        meta = pd.DataFrame({
            "项目": ["更新时间(UTC)", "数据源", "口径", "汇率USD/CNY", "提示"],
            "值": [stamp, "USDA NASS Quick Stats / Price Received / SURVEY / NATIONAL",
                   "农场出售端价格收到价(月度),非中国零售口径",
                   FX_USD_CNY,
                   "元/公斤列按当前汇率统一换算,历史早期行不代表当年实际人民币价"],
        })
        meta.to_excel(xw, sheet_name="说明", index=False)
        for name, cfg in CATS.items():
            raw = fetch(cfg["commodity"])
            raw.to_csv(f"{OUT_RAW}/{name}_price.csv", index=False)   # 原始存档
            proc = process(raw, cfg)
            proc.to_excel(xw, sheet_name=cfg["cn"], index=False)
            all_proc.append(proc)
            print(f"[OK] {name}: 原始{len(raw)}行, 月度主指标{len(proc)}行, "
                  f"最新 {proc['date'].max()} = {proc['价格_美制'].iloc[-1]} {cfg['unit']}")
    pd.concat(all_proc).to_csv(f"{OUT_PROC}/USDA_价格_合并长表.csv", index=False)
    print(f"[DONE] {stamp}")


if __name__ == "__main__":
    main()
