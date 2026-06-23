# WeatherLab MVP 爬蟲任務

## 目標
爬取 ECMWF、ICON、GFS 三個模型的高低溫預測，每天 2 次快照（07:00、19:00 JST），寫到 Google Sheet。

## 時間框
- 開始：06-23（現在）
- 完成：06-26 前能跑起來

## 數據源與欄位對應

### 天氣預測（選一種方案）

**方案 A - Open-Meteo API（推薦，完全免費）**
- 單次 API 調用取 3 個模型預測
- 無需認證，無速度限制
- Endpoint：`https://api.open-meteo.com/v1/forecast`
- 參數：
  - `latitude`、`longitude`
  - `models=ecmwf_ifs025,icon_seamless,gfs025`
  - `temperature_2m`、`forecast_days=2`

**方案 B - 各模型官方 API**
- ECMWF：需申請免費帳號
- GFS：NOAA 公開數據
- ICON：德國氣象局公開數據
- 複雜度較高

**推薦：用 Open-Meteo 方案 A**

### 實際溫度數據
- NOAA 歷史天氣：https://www.ncei.noaa.gov/data/global-summary-of-the-day/
- 或 Open-Meteo Archive API（同樣免費）

## 城市清單
```
Tokyo (35.6762, 139.6503)
Hong Kong (22.3193, 114.1694)
Singapore (1.3521, 103.8198)
New York City (40.7128, -74.0060)
London (51.5074, -0.1278)
```

## 輸出格式
Google Sheet `WeatherLab Data` 的 Master Table
- 每次爬蟲 append 一行
- 欄位：snapshot_time, forecast_date, city, ecmwf_high, icon_high, gfs_high, ecmwf_low, icon_low, gfs_low
- actual_high、actual_low 先留空（後續補）

## 實現方式

**選項 1 - 本地手動跑**（MVP 快速驗證）
- Python 腳本（requests + gspread）
- 手動每天 07:00、19:00 執行：`python scraper.py`
- 時間：1 小時左右

**選項 2 - GitHub Actions 自動化**（完整方案）
- 用 GitHub Actions 定時跑（cron）
- 需要配置 Google Sheets API credentials
- 時間：2-3 小時

**MVP 建議：先做選項 1，14 天後再考慮自動化**

## 交付物
- `scrapers/weather_forecast.py` — 主爬蟲
- `README.md` — 使用說明
- Google Sheet 能正常 append 數據

## Checklist
- [ ] 確認 Open-Meteo API 三個模型都能取到
- [ ] 城市坐標驗證
- [ ] Google Sheets API 認證（gspread）
- [ ] 本地跑過 1 次，確認數據寫入 Sheet
- [ ] 確認預測時間與實際時間的對應關係

## 問題排查
- 三模型返回不同解析度？→ 取最近的可用數據
- 預測日期格式不一致？→ 統一為 YYYY-MM-DD
- API 速度慢？→ 並行調用 5 個城市

---

**時間承諾**：如果 Open-Meteo 方案行得通，完整開發 + 測試應該 3-5 小時內能完成。
