#!/usr/bin/env python3
"""
USDA NASS Quick Stats 自动抓取脚本
- 拉取 7 个品类的"价格收到价(Price Received)"全国月度历史
- 保存原始 CSV(存档)
- 生成与历史趋势模板一致的宽表 Excel: 参数页、品类页、公式列、趋势图
- key 从环境变量 NASS_API_KEY 读取(GitHub Secret),不写死在代码里

本地测试:
  NASS_API_KEY=你的key python fetch_usda.py
  USE_EXISTING_RAW=1 NASS_API_KEY=dummy python fetch_usda.py
"""
import datetime as dt
import io
import os
import sys

import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import LineChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


KEY = os.environ.get("NASS_API_KEY")
if not KEY:
    sys.exit("缺少环境变量 NASS_API_KEY")

FX_USD_CNY = float(os.environ.get("FX_USD_CNY", "6.77"))
BASE = "https://quickstats.nass.usda.gov/api/api_GET/"
OUT_RAW = "data/raw"
OUT_PROC = "data"
os.makedirs(OUT_RAW, exist_ok=True)
os.makedirs(OUT_PROC, exist_ok=True)

BLUE = "1F4E78"
GREY = "808080"
LIGHT_BLUE = "DDEBF7"
YELLOW = "FFFF00"
WHITE = "FFFFFF"
THIN_BORDER = Border(
    left=Side(style="thin", color="9EADCC"),
    right=Side(style="thin", color="9EADCC"),
    top=Side(style="thin", color="9EADCC"),
    bottom=Side(style="thin", color="9EADCC"),
)


CATS = {
    "cattle": {
        "commodity": "CATTLE",
        "cn": "牛肉·活牛",
        "unit": "$ / CWT",
        "unit_cn": "美元/英担(cwt=100磅)",
        "kg": 45.3592,
        "main_label": "肉牛(≥500磅,≈活牛)",
        "metrics": [
            ("肉牛(≥500磅,≈活牛)\n($ / CWT)", "CATTLE, GE 500 LBS - PRICE RECEIVED, MEASURED IN $ / CWT"),
            ("阉公牛及小母牛(≥500磅)\n($ / CWT)", "CATTLE, STEERS & HEIFERS, GE 500 LBS - PRICE RECEIVED, MEASURED IN $ / CWT"),
            ("犊牛(Calves)\n($ / CWT)", "CATTLE, CALVES - PRICE RECEIVED, MEASURED IN $ / CWT"),
            ("母牛(Cows)\n($ / CWT)", "CATTLE, COWS - PRICE RECEIVED, MEASURED IN $ / CWT"),
        ],
    },
    "hogs": {
        "commodity": "HOGS",
        "cn": "生猪",
        "unit": "$ / CWT",
        "unit_cn": "美元/英担(cwt=100磅)",
        "kg": 45.3592,
        "main_label": "生猪(全部)",
        "metrics": [
            ("生猪(全部)\n($ / CWT)", "HOGS - PRICE RECEIVED, MEASURED IN $ / CWT"),
            ("阉公猪及后备母猪(育肥猪)\n($ / CWT)", "HOGS, BARROWS & GILTS - PRICE RECEIVED, MEASURED IN $ / CWT"),
            ("母猪(Sows)\n($ / CWT)", "HOGS, SOWS - PRICE RECEIVED, MEASURED IN $ / CWT"),
        ],
    },
    "milk": {
        "commodity": "MILK",
        "cn": "生鲜乳",
        "unit": "$ / CWT",
        "unit_cn": "美元/英担(cwt=100磅)",
        "kg": 45.3592,
        "main_label": "生鲜乳(全部)",
        "metrics": [
            ("生鲜乳(全部)\n($ / CWT)", "MILK - PRICE RECEIVED, MEASURED IN $ / CWT"),
            ("饮用级(Fluid grade)\n($ / CWT)", "MILK, FLUID GRADE - PRICE RECEIVED, MEASURED IN $ / CWT"),
            ("制造级(Manufacturing)\n($ / CWT)", "MILK, MANUFACTURING GRADE - PRICE RECEIVED, MEASURED IN $ / CWT"),
        ],
    },
    "eggs": {
        "commodity": "EGGS",
        "cn": "鸡蛋",
        "unit": "$ / DOZEN",
        "unit_cn": "美元/打(12枚)",
        "kg": 0.68,
        "main_label": "鸡蛋(全部)",
        "metrics": [
            ("鸡蛋(全部)\n($ / DOZEN)", "EGGS - PRICE RECEIVED, MEASURED IN $ / DOZEN"),
            ("商品蛋(Table eggs)\n($ / DOZEN)", "EGGS, TABLE - PRICE RECEIVED, MEASURED IN $ / DOZEN"),
        ],
    },
    "chickens": {
        "commodity": "CHICKENS",
        "cn": "鸡肉",
        "unit": "$ / LB",
        "unit_cn": "美元/磅(lb)",
        "kg": 0.453592,
        "main_label": "肉鸡(Broilers)",
        "metrics": [
            ("肉鸡(Broilers)\n($ / LB)", "CHICKENS, BROILERS - PRICE RECEIVED, MEASURED IN $ / LB"),
        ],
    },
    "corn": {
        "commodity": "CORN",
        "cn": "玉米",
        "unit": "$ / BU",
        "unit_cn": "美元/蒲式耳(玉米1bu=25.40kg)",
        "kg": 25.4012,
        "main_label": "玉米(Corn grain)",
        "metrics": [
            ("玉米(Corn grain)\n($ / BU)", "CORN, GRAIN - PRICE RECEIVED, MEASURED IN $ / BU"),
        ],
    },
    "soybeans": {
        "commodity": "SOYBEANS",
        "cn": "大豆",
        "unit": "$ / BU",
        "unit_cn": "美元/蒲式耳(大豆1bu=27.22kg)",
        "kg": 27.2155,
        "main_label": "大豆(Soybeans)",
        "metrics": [
            ("大豆(Soybeans)\n($ / BU)", "SOYBEANS - PRICE RECEIVED, MEASURED IN $ / BU"),
        ],
    },
}


def fetch(commodity: str) -> pd.DataFrame:
    import requests

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
        raise RuntimeError(f"{commodity} 请求失败 HTTP {r.status_code}: {r.text[:300]}")
    return pd.read_csv(io.StringIO(r.text), low_memory=False)


def fetch_or_load(name: str, cfg: dict) -> pd.DataFrame:
    raw_path = f"{OUT_RAW}/{name}_price.csv"
    if os.environ.get("USE_EXISTING_RAW") == "1" and os.path.exists(raw_path):
        return pd.read_csv(raw_path, low_memory=False)
    raw = fetch(cfg["commodity"])
    raw.to_csv(raw_path, index=False)
    return raw


def clean_values(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype(str).str.replace(",", "", regex=False), errors="coerce")


def metric_series(raw: pd.DataFrame, cfg: dict, short_desc: str) -> pd.Series:
    d = raw[
        (raw["freq_desc"] == "MONTHLY")
        & (raw["unit_desc"] == cfg["unit"])
        & (raw["agg_level_desc"] == "NATIONAL")
        & (raw["short_desc"] == short_desc)
    ].copy()
    d["Value"] = clean_values(d["Value"])
    d["date"] = pd.to_datetime(
        {
            "year": d["year"].astype(int),
            "month": d["begin_code"].astype(int),
            "day": 1,
        }
    )
    d = d.dropna(subset=["Value"]).sort_values("date")
    return d.groupby("date")["Value"].last()


def build_wide(raw: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    series_by_header = {}
    for header, short_desc in cfg["metrics"]:
        series_by_header[header] = metric_series(raw, cfg, short_desc)
    wide = pd.DataFrame(series_by_header).sort_index()
    wide = wide.dropna(how="all")
    wide.insert(0, "月", wide.index.month)
    wide.insert(0, "年", wide.index.year)
    wide.insert(0, "日期", wide.index.to_pydatetime())
    return wide.reset_index(drop=True)


def build_long_for_csv(wide: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    main_header = cfg["metrics"][0][0]
    out = wide[["日期", "年", main_header]].copy()
    out = out.rename(columns={"日期": "date", main_header: "价格_美制"})
    out.insert(0, "品类", cfg["cn"])
    out.insert(2, "美制单位", cfg["unit"])
    out["价格_元每公斤"] = (out["价格_美制"] * FX_USD_CNY / cfg["kg"]).round(3)
    out["环比"] = out["价格_美制"].pct_change().round(4)
    out["同比"] = out["价格_美制"].pct_change(12).round(4)
    return out


def apply_info_styles(ws):
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 16
    ws.column_dimensions["C"].width = 70
    ws.row_dimensions[1].height = 23.85
    ws["A1"].font = Font(name="Arial", size=16, bold=True, color=BLUE)
    ws["A2"].font = Font(name="Noto Sans CJK SC", size=10)
    ws["A3"].font = Font(name="Noto Sans CJK SC", size=10, color=GREY)
    for cell in ["A5", "A8", "A15", "A24"]:
        ws[cell].font = Font(name="Noto Sans CJK SC", size=12, bold=True)
    ws["A6"].font = Font(name="Noto Sans CJK SC", size=11, bold=True)
    ws["B6"].font = Font(name="Arial", size=11, bold=True, color="0000FF")
    ws["B6"].fill = PatternFill("solid", fgColor=YELLOW)
    ws["B6"].number_format = "0.0000"
    for row in range(16, 22):
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=3)
        ws.cell(row, 1).alignment = Alignment(wrap_text=True, vertical="top")
    for cell in ws[25]:
        cell.fill = PatternFill("solid", fgColor=BLUE)
        cell.font = Font(name="Noto Sans CJK SC", size=10, bold=True, color=WHITE)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = THIN_BORDER
    for row in range(1, ws.max_row + 1):
        for cell in ws[row]:
            if not cell.font or cell.font.name == "Calibri":
                cell.font = Font(name="Noto Sans CJK SC", size=10)


def write_info_sheet(wb: Workbook, summaries: list[dict], export_date: str):
    ws = wb.active
    ws.title = "参数与说明"
    rows = [
        ["USDA 美国畜牧·饲料价格 — 历史趋势", None, None],
        ["数据来源：USDA NASS Quick Stats（价格收到价 Price Received，全国 SURVEY）", None, None],
        [f"来源接口：https://quickstats.nass.usda.gov/api/  | 导出日期：{export_date}", None, None],
        [None, None, None],
        ["可调参数", None, None],
        ["美元兑人民币汇率", FX_USD_CNY, f"改这里→所有Sheet的「元/公斤」列自动重算。来源：运行参数 FX_USD_CNY={FX_USD_CNY}，仅作换算口径。"],
        [None, None, None],
        ["单位换算系数（每单位含多少公斤）", None, None],
        ["cwt(英担)", "45.3592 kg", "牛/猪/奶：$/cwt ÷45.3592 ×汇率 = 元/公斤"],
        ["lb(磅)", "0.453592 kg", "鸡肉：$/lb ÷0.453592 ×汇率 = 元/公斤"],
        ["bu 玉米", "25.4012 kg", "玉米：$/bu ÷25.4012 ×汇率 = 元/公斤"],
        ["bu 大豆", "27.2155 kg", "大豆：$/bu ÷27.2155 ×汇率 = 元/公斤"],
        ["打(鸡蛋)", "≈0.68 kg", "鸡蛋：$/打 ÷0.68 ×汇率 = 元/公斤（含壳近似，仅参考）"],
        [None, None, None],
        ["口径与解读提示（重要）", None, None],
        ["1) 这是美国「农场出售端价格收到价」(monthly)，对应中国农业农村部的产业链上游/农场价，不是县集贸市场零售价；与中国零售口径不完全可比。", None, None],
        ["2) 各Sheet「趋势图」用美制原值绘制（真实历史）。", None, None],
        ["3) 「元/公斤」列用当前汇率统一换算，仅为与中国现价对比；历史早期行不代表当年实际人民币价格（汇率/币值已大不同），勿据此读历史人民币走势。", None, None],
        ["4) 环比=与上月比，同比=与去年同月比，按日期精确匹配（缺月自动留空）。", None, None],
        ["5) boxed beef分割肉批发价、屠宰量/胴重等NASS无，需USDA市场新闻(MMN)补。", None, None],
        ["6) 已剔除PCT OF PARITY(平价百分比)及$/HEAD等非价格口径，只保留$/单位价格。", None, None],
        [None, None, None],
        [None, None, None],
        ["各Sheet概览", None, None],
        ["品类", "月度行数", "时间范围 / 主指标"],
    ]
    for summary in summaries:
        rows.append([summary["cn"], summary["rows"], summary["range_text"]])
    for row in rows:
        ws.append(row)
    apply_info_styles(ws)


def unit_formula_text(cfg: dict) -> str:
    return f"{cfg['unit_cn']}；元/公斤=主指标×汇率('参数与说明'!$B$6)÷{cfg['kg']}。趋势图用美制原值。"


def add_chart(ws, cfg: dict, max_row: int, max_col: int):
    chart = LineChart()
    chart.title = f"{cfg['cn']} 价格历史趋势（{cfg['unit']}，主指标:{cfg['main_label']}）"
    chart.y_axis.title = cfg["unit"]
    chart.y_axis.numFmt = "#,##0.00"
    chart.x_axis.number_format = "yyyy-mm"
    chart.legend.position = "r"
    chart.height = 7.5
    chart.width = 15
    data = Reference(ws, min_col=4, max_col=4, min_row=4, max_row=max_row)
    cats = Reference(ws, min_col=1, min_row=5, max_row=max_row)
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(cats)
    ws.add_chart(chart, f"{get_column_letter(max_col + 2)}5")


def write_category_sheet(wb: Workbook, cfg: dict, wide: pd.DataFrame):
    ws = wb.create_sheet(cfg["cn"])
    ws.sheet_view.showGridLines = False
    ws.freeze_panes = "D5"
    ws["A1"] = f"{cfg['cn']} — USDA NASS 价格收到价（月度）"
    ws["A2"] = unit_formula_text(cfg)
    ws["A1"].font = Font(name="Noto Sans CJK SC", size=13, bold=True, color=BLUE)
    ws["A2"].font = Font(name="Noto Sans CJK SC", size=9, color=GREY)
    ws.row_dimensions[1].height = 20.1
    ws.row_dimensions[2].height = 15

    metric_headers = [header for header, _ in cfg["metrics"]]
    headers = ["日期", "年", "月", *metric_headers, "主指标(元/公斤)", "环比%", "同比%"]
    for col, header in enumerate(headers, 1):
        cell = ws.cell(4, col, header)
        cell.fill = PatternFill("solid", fgColor=BLUE)
        cell.font = Font(name="Noto Sans CJK SC", size=9, bold=True, color=WHITE)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = THIN_BORDER
    ws.row_dimensions[4].height = 37.3 if len(metric_headers) >= 3 else 24.6

    rmb_col = 4 + len(metric_headers)
    mom_col = rmb_col + 1
    yoy_col = rmb_col + 2

    for idx, record in wide.iterrows():
        row = idx + 5
        ws.cell(row, 1, record["日期"])
        ws.cell(row, 2, int(record["年"]))
        ws.cell(row, 3, int(record["月"]))
        for offset, header in enumerate(metric_headers, 4):
            value = record[header]
            ws.cell(row, offset, None if pd.isna(value) else float(value))
        ws.cell(row, rmb_col, f'=IF(D{row}="","",D{row}*\'参数与说明\'!$B$6/{cfg["kg"]})')
        ws.cell(row, mom_col, f'=IFERROR(IF(D{row}="","",D{row}/INDEX($D:$D,MATCH(EDATE($A{row},-1),$A:$A,0))-1),"")')
        ws.cell(row, yoy_col, f'=IFERROR(IF(D{row}="","",D{row}/INDEX($D:$D,MATCH(EDATE($A{row},-12),$A:$A,0))-1),"")')

    max_row = len(wide) + 4
    max_col = len(headers)
    widths = [10, 7, 6]
    widths.extend([16] + [13] * (len(metric_headers) - 1))
    widths.extend([14, 9, 13])
    for col, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(col)].width = width
    for row in range(5, max_row + 1):
        use_fill = row % 2 == 0
        for col in range(1, max_col + 1):
            cell = ws.cell(row, col)
            cell.font = Font(name="Arial", size=9)
            if use_fill:
                cell.fill = PatternFill("solid", fgColor=LIGHT_BLUE)
        ws.cell(row, 1).number_format = "yyyy-mm"
        ws.cell(row, 2).number_format = "0"
        ws.cell(row, 3).number_format = "0"
        for col in range(4, rmb_col + 1):
            ws.cell(row, col).number_format = "#,##0.00"
        ws.cell(row, mom_col).number_format = "0.0%"
        ws.cell(row, yoy_col).number_format = "0.0%"
    add_chart(ws, cfg, max_row, max_col)


def build_workbook(wide_by_name: dict[str, pd.DataFrame], export_date: str):
    wb = Workbook()
    try:
        wb.calculation.fullCalcOnLoad = True
        wb.calculation.forceFullCalc = True
    except AttributeError:
        pass
    summaries = []
    for name, cfg in CATS.items():
        wide = wide_by_name[name]
        min_date = wide["日期"].min().strftime("%Y-%m")
        max_date = wide["日期"].max().strftime("%Y-%m")
        summaries.append({
            "cn": cfg["cn"],
            "rows": len(wide),
            "range_text": f"{min_date} ~ {max_date}  | 主:{cfg['main_label']}",
        })
    write_info_sheet(wb, summaries, export_date)
    for name, cfg in CATS.items():
        write_category_sheet(wb, cfg, wide_by_name[name])
    return wb


def main():
    now_utc = dt.datetime.now(dt.UTC)
    stamp = now_utc.strftime("%Y-%m-%d %H:%M UTC")
    export_date = now_utc.strftime("%Y-%m-%d")
    wide_by_name = {}
    all_proc = []
    for name, cfg in CATS.items():
        raw = fetch_or_load(name, cfg)
        wide = build_wide(raw, cfg)
        wide_by_name[name] = wide
        all_proc.append(build_long_for_csv(wide, cfg))
        main_header = cfg["metrics"][0][0]
        print(f"[OK] {name}: 原始{len(raw)}行, 月度主指标{len(wide)}行, "
              f"最新 {wide['日期'].max().date()} = {wide[main_header].iloc[-1]} {cfg['unit']}")

    workbook = build_workbook(wide_by_name, export_date)
    workbook.save(f"{OUT_PROC}/USDA畜牧饲料价格_自动更新.xlsx")
    pd.concat(all_proc).to_csv(f"{OUT_PROC}/USDA_价格_合并长表.csv", index=False)
    print(f"[DONE] {stamp}")


if __name__ == "__main__":
    main()
