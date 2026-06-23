# USDA 价格自动定时抓取（GitHub Actions）

每周自动从 USDA NASS Quick Stats 拉取 7 个品类（牛/猪/奶/蛋/鸡/玉米/大豆）的"价格收到价"全国月度历史，
存档原始 CSV，并生成带「元/公斤换算 + 环比 + 同比」的整理表，自动提交回仓库。无需电脑常开，免费。

## 一次性部署（约 5 分钟）

1. **建一个 GitHub 仓库**（私有即可），把本文件夹里的内容放进去，保持目录结构：
   ```
   fetch_usda.py
   requirements.txt
   .github/workflows/usda.yml
   README.md
   ```

2. **加 API key 到 Secrets**（不要把 key 写进代码或提交到仓库）：
   仓库页 → Settings → Secrets and variables → Actions → New repository secret
   - Name: `NASS_API_KEY`
   - Secret: 你的 NASS key（即 `E089D28A-...`）

3. **确认 Actions 有写权限**：
   Settings → Actions → General → Workflow permissions → 选 **Read and write permissions** → Save。

4. **跑一次验证**：
   Actions 标签页 → 选 "USDA NASS 价格自动抓取" → 右侧 **Run workflow**（手动触发）。
   成功后 `data/` 目录会出现：
   - `data/raw/*.csv`（7 个原始存档，与手动下载同源）
   - `data/USDA畜牧饲料价格_自动更新.xlsx`（含说明页 + 各品类整理表）
   - `data/USDA_价格_合并长表.csv`（所有品类合并的长表，便于做图/透视）

## 定时频率

默认 **每周一 00:00 UTC（北京时间周一 08:00 早上）**。
NASS 月度数据每月更新一次，每周足够。要改频率，编辑 `.github/workflows/usda.yml` 里的 `cron`。
（cron 用 UTC 时区。）

## 换汇率

`元/公斤` 列按汇率换算，默认 6.77。改 `usda.yml` 里 `FX_USD_CNY` 的值即可。

## 口径提示（重要）

- 这是**农场出售端价格收到价**（≈产业链上游/农场价），不是中国农业农村部那种县集贸**零售**价，两边不完全可比。
- `元/公斤` 用**当前汇率**统一换算，仅为与中国现价对比；历史早期行不代表当年实际人民币价格。
- boxed beef 分割肉批发价、屠宰量/胴重 NASS 没有，需另接 USDA Market News (MMN) API（另一个免费 key）。要加我再给你扩展脚本。

## 本地先试（可选）

```bash
pip install -r requirements.txt
NASS_API_KEY=你的key python fetch_usda.py
```

## 数据来源

- NASS Quick Stats: https://quickstats.nass.usda.gov/
- 官方月报 Agricultural Prices: https://usda.library.cornell.edu/concern/publications/c821gj76b
