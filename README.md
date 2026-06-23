# 各国农业数据自动周报

这个仓库每周一自动生成并邮件发送：

- 美国 USDA NASS 农业数据 Excel
- 中国农业农村部监测预警 Excel
- 澳洲农业数据 Excel
- 各国农业数据合并看板 zip
- 看板所用 `dashboard-data.json`

看板 zip 解压后直接打开 `各国农业数据监测看板.html`，不需要手动上传 Excel 或数据文件。

## 自动运行频率

GitHub Actions 默认每周一 `00:00 UTC` 运行，也就是北京时间周一早上 `08:00`。

workflow 文件：

```text
.github/workflows/weekly-agri-monitor.yml
```

## GitHub Secrets

仓库页进入：

`Settings` → `Secrets and variables` → `Actions` → `New repository secret`

需要配置：

| Secret | 用途 |
|---|---|
| `NASS_API_KEY` | USDA NASS Quick Stats API key |
| `SMTP_HOST` | 邮箱 SMTP 服务器，例如 `smtp.gmail.com` |
| `SMTP_PORT` | SMTP 端口，通常 `587`；SSL 可用 `465` |
| `SMTP_USERNAME` | 发件邮箱账号 |
| `SMTP_PASSWORD` | 发件邮箱 SMTP 授权码/应用专用密码 |
| `MAIL_TO` | 收件邮箱 |
| `MAIL_FROM` | 发件邮箱，可选；不填默认使用 `SMTP_USERNAME` |

还要确认：

`Settings` → `Actions` → `General` → `Workflow permissions` 选择 `Read and write permissions`。

## 输出文件

USDA 数据会写入仓库 `data/`：

```text
data/美国USDA农业数据_YYYY-MM-DD_HHMM.xlsx
data/美国USDA农业数据_YYYY-MM-DD_HHMM_合并长表.csv
data/raw/*.csv
```

农业农村部历史基线和澳洲本地长表会写入：

```text
data/moa/baseline_moa_jcyj_data.json
data/澳洲农业数据_YYYY-MM-DD_HHMM_合并长表.csv
```

每周邮件附件包含：

```text
美国USDA农业数据_YYYY-MM-DD_HHMM.xlsx
中国农业农村部监测预警数据汇总_YYYYMMDD_清洗版.xlsx
澳洲农业数据_YYYY-MM-DD_HHMM.xlsx
各国农业数据监测看板.zip
dashboard-data.json
```

## 本地测试

```bash
python -m pip install -r requirements.txt

NASS_API_KEY=你的key python fetch_usda.py

node scripts/scrape_moa_jcyj.mjs --out-dir=dist/moa --pages=1

python scripts/merge_moa_data.py \
  --baseline data/moa/baseline_moa_jcyj_data.json \
  --latest dist/moa/moa_jcyj_data.json \
  --output dist/moa/moa_jcyj_data_merged.json

python scripts/build_moa_workbook.py \
  --input dist/moa/moa_jcyj_data_merged.json \
  --template templates/moa_chart_format_template.xlsx \
  --output dist

US_CSV="$(ls -1t data/美国USDA农业数据_*_合并长表.csv | head -n 1)"
AU_CSV="$(ls -1t data/澳洲农业数据_*_合并长表.csv | head -n 1)"

python scripts/build_australia_workbook.py \
  --input "$AU_CSV" \
  --output-dir dist

python scripts/build_china_us_agri_dashboard_data.py \
  --moa-json dist/moa/moa_jcyj_data_merged.json \
  --usda-csv "$US_CSV" \
  --australia-csv "$AU_CSV" \
  --output dist/dashboard/data/dashboard-data.json

python scripts/prepare_china_us_dashboard.py \
  --template dashboard/中美农业数据监测看板.html \
  --data dist/dashboard/data/dashboard-data.json \
  --output-dir dist/dashboard \
  --zip-name 各国农业数据监测看板.zip
```

## 数据来源口径

- 中国：农业农村部畜牧兽医局监测预警，周度数据。
- 美国：USDA NASS Quick Stats，月度 Price Received 数据。
- 澳洲：MLA/NLRS、Australian Pork/ProFarmer、ABS CPI 与 ABS Livestock Products 等本地抓取后的公开数据。
- 单国视图保留各自原始单位；各国对比只展示同商品、同价格口径可进入比较的国家，并统一换算为元/kg。
