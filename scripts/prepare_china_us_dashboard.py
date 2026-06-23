#!/usr/bin/env python3
import argparse
import json
import shutil
import zipfile
from pathlib import Path


def read_json_payload(data_file):
    text = data_file.read_text(encoding="utf-8").strip()
    if text.startswith("window.CHINA_US_AGRI_DASHBOARD_DATA"):
        text = text.split("=", 1)[1].strip()
        if text.endswith(";"):
            text = text[:-1]
    return json.loads(text)


def main():
    parser = argparse.ArgumentParser(description="打包各国农业数据监测看板")
    parser.add_argument("--template", required=True, help="看板 HTML 模板")
    parser.add_argument("--data", required=True, help="dashboard-data.json")
    parser.add_argument("--output-dir", required=True, help="看板输出目录")
    parser.add_argument("--zip-name", default="各国农业数据监测看板.zip")
    args = parser.parse_args()

    template = Path(args.template)
    data_file = Path(args.data)
    output_dir = Path(args.output_dir)
    output_data_dir = output_dir / "data"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_data_dir.mkdir(parents=True, exist_ok=True)

    dashboard_html = output_dir / "各国农业数据监测看板.html"
    dashboard_data = output_data_dir / "dashboard-data.json"
    payload = read_json_payload(data_file)
    payload_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    html = template.read_text(encoding="utf-8")
    html = html.replace(
        '<script id="dashboard-data-json" type="application/json"></script>',
        f'<script id="dashboard-data-json" type="application/json">{payload_text}</script>',
    )
    dashboard_html.write_text(html, encoding="utf-8")
    if data_file.resolve() != dashboard_data.resolve():
        shutil.copy2(data_file, dashboard_data)

    zip_path = output_dir.parent / args.zip_name
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(dashboard_html, dashboard_html.name)
        archive.write(dashboard_data, "data/dashboard-data.json")

    print({"dashboard": str(dashboard_html), "data": str(dashboard_data), "zip": str(zip_path)})


if __name__ == "__main__":
    main()
