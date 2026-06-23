#!/usr/bin/env python3
import argparse
import shutil
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="打包中美农业数据监测看板")
    parser.add_argument("--template", required=True, help="看板 HTML 模板")
    parser.add_argument("--data", required=True, help="dashboard-data.js")
    parser.add_argument("--output-dir", required=True, help="看板输出目录")
    parser.add_argument("--zip-name", default="中美农业数据监测看板.zip")
    args = parser.parse_args()

    template = Path(args.template)
    data_file = Path(args.data)
    output_dir = Path(args.output_dir)
    output_data_dir = output_dir / "data"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_data_dir.mkdir(parents=True, exist_ok=True)

    dashboard_html = output_dir / "中美农业数据监测看板.html"
    dashboard_data = output_data_dir / "dashboard-data.js"
    shutil.copy2(template, dashboard_html)
    if data_file.resolve() != dashboard_data.resolve():
        shutil.copy2(data_file, dashboard_data)

    zip_path = output_dir.parent / args.zip_name
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(dashboard_html, dashboard_html.name)
        archive.write(dashboard_data, "data/dashboard-data.js")

    print({"dashboard": str(dashboard_html), "data": str(dashboard_data), "zip": str(zip_path)})


if __name__ == "__main__":
    main()
