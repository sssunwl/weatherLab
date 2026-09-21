# WeatherLab MVP

> **2026-09-21 v1 收掉**:預測拿去買 Polymarket 都買不中。診斷是 v1 量錯東西(UTC 切日、市中心座標、再分析格點當實測、只有點預測),不是模型本身。v2 方向見 [docs/IMPROVE.md](docs/IMPROVE.md)。排程(本機 cron、GitHub Actions)都已停。

建立全球天氣預測驗證系統，找出不同城市中 ECMWF、ICON、GFS 的實際準確率。

## 目標
- **Phase 1（MVP）**：2週內驗證三個模型在5個城市的預測準確度
- **Phase 2**：如果有明顯 edge，完整自動化系統

## 快速開始

### 爬蟲任務
見 `SCRAPER_TASK.md`

### Google Sheet
數據儲存：https://docs.google.com/spreadsheets/d/1u54xWukYo5gQ49PmW6FosYNBfJOepV5o1yaF6_o1Z7M/edit

## 執行紀錄

### 2026-08-26

- 19:00 JST 排程已成功抓取 Tokyo、Hong Kong、Singapore、New York City、London 五個城市資料，顯示前次 DNS／網路連線問題已解除；寫入 Google Sheet 時仍因 `invalid_grant` 失敗，確認 OAuth refresh token 尚未更新。
- 下一步：重新授權 Google Sheet 並更新 token，手動補跑 8/23、8/24 與 8/26 失敗的預報／實況資料，再核對缺漏日期及五城資料。

### 2026-08-24

- 09:01 JST 的實況更新未能連上 `oauth2.googleapis.com`，Google Sheet 讀取／寫入流程因 DNS／網路連線錯誤中止；前一日的 OAuth refresh token 失效問題仍未確認解除。
- 下一步：先恢復網路並重新授權 Google Sheet，手動補跑預報與實況更新，再核對缺漏日期與五個城市資料是否完整追加。

### 2026-08-23

- 20:00 JST 的排程成功抓取 Tokyo、Hong Kong、Singapore、New York City、London 五個城市資料，但寫入 Google Sheet 時因 OAuth refresh token 已過期或撤銷而失敗。
- 下一步：重新授權 Google Sheet 憑證後手動補跑，並確認五筆資料已成功追加。
