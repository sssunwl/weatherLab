# weatherLab v2 派工單(給 Codex)

> 寫於 2026-09-22。規劃:Claude。實作:Codex。審查:Claude。
> 背景診斷見 `docs/IMPROVE.md`,但**本文件自帶所有數字,以本文件為準**。

## 0. 硬性規則(先讀)

1. **不要 commit、不要碰 `.git`**。分支 `v2` 已經開好,改完留在工作目錄,Claude 審完自己 commit。
2. **v1 檔案一律不動**:`scrapers/weather_forecast.py`、`scrapers/weather_forecast_oauth.py`、`scrapers/update_actuals.py`、`SCRAPER_TASK.md`、`README.md`、`cron.log`、`docs/IMPROVE.md`。唯一例外:刪掉 `.github/workflows/scrape_weather.yml`(v1 排程,已停用)。
3. `scrapers/notify_discord.py` 已經存在且測過,**直接 import 用,不要改**(提供 `send(text)`;讀環境變數 `DISCORD_WEBHOOK_URL`,沒有就讀 `~/.config/weatherlab/discord_webhook`)。
4. **任何秘密都不進 repo**。Discord webhook 只從環境變數讀。repo 是 public。
5. 只用 Python 3.11 標準函式庫 + `requests`。不要 numpy / pandas / scipy(常態分布 CDF 用 `math.erf`)。前端純 HTML/CSS/JS,不用框架、不用 CDN。
6. 單元測試不打網路。你的沙箱如果連不了網路,照本文件寫的 API 格式實作,並在最後的回報清楚列出「哪些沒實際打過 API」。**不要自己猜 API 格式**,本文件的格式都是 Claude 實際打過確認的。
7. 本文件沒要求的東西不要加(不加 log 系統、不加設定檔框架、不重構)。有疑問寫在回報裡,不要自己決定。

## 1. 要做什麼(一句話)

每天兩次:抓 Polymarket 天氣市場「某城市某天最高溫落在哪一格」的市價,用 ensemble 預報算出我們自己的每格機率,記錄下來;結算後拿**市場指定測站的實測**對答案;用紙上交易(不花真錢)累積成績;每天推一則 Discord;GitHub Pages 儀表板顯示細節。

**這一版不做**:結算當天每小時的即時更新(之後另開派工單)、最低溫市場、真錢下單。

## 2. 市場設定 `config/markets.json`

建這個檔,內容照抄(座標是測站,不是市中心):

```json
[
  {
    "key": "nyc", "name": "紐約 LGA", "enabled": true,
    "slug_city": "nyc",
    "station": "KLGA", "iem_station": "LGA",
    "lat": 40.7794, "lon": -73.8803,
    "tz": "America/New_York",
    "unit": "F", "settle_rule": "round", "kernel_sigma": 1.8
  },
  {
    "key": "london", "name": "倫敦 LCY", "enabled": true,
    "slug_city": "london",
    "station": "EGLC", "iem_station": "EGLC",
    "lat": 51.5053, "lon": 0.0553,
    "tz": "Europe/London",
    "unit": "C", "settle_rule": "round", "kernel_sigma": 1.0
  },
  {
    "key": "hong-kong", "name": "香港 天文台", "enabled": true,
    "slug_city": "hong-kong",
    "station": "HKO", "iem_station": null,
    "lat": 22.3019, "lon": 114.1742,
    "tz": "Asia/Hong_Kong",
    "unit": "C", "settle_rule": "floor", "kernel_sigma": 1.0
  },
  {
    "key": "miami", "name": "邁阿密 MIA", "enabled": false,
    "slug_city": "miami",
    "station": "KMIA", "iem_station": "MIA",
    "lat": 25.7881, "lon": -80.3169,
    "tz": "America/New_York",
    "unit": "F", "settle_rule": "round", "kernel_sigma": 1.8
  }
]
```

`enabled: false` 的市場整條流程都跳過。

### 2.1 結算規則(Claude 已用過去 25~31 天的已結算市場驗證)

「目標日」= **測站當地時區的日曆日**(00:00~24:00 當地時間,要正確處理夏令時間,那天可能 23 或 25 小時)。

| 市場 | 結算值怎麼來 | 進位 | 驗證結果 |
|---|---|---|---|
| nyc / miami | 該當地日所有 METAR(例行+特別報)的最高氣溫 °F | 四捨五入成整數 °F(`floor(x+0.5)`) | 25/25、24/25 吻合 |
| london | 該當地日所有 METAR(例行+特別報)的最高氣溫 °C(METAR 本身就是整數) | 四捨五入成整數 °C | 25/25 吻合 |
| hong-kong | 天文台 Daily Extract 的「絕對最高氣溫」(小數一位) | **無條件捨去**成整數(36.9 → 36°C 那格) | 31/31 吻合 |

`settle_rule` 就是這一欄:`round` = `floor(x+0.5)`,`floor` = `floor(x)`。**不要用 Python 的 `round()`**(它是銀行家進位,72.5 會變 72)。

### 2.2 格子(bucket)解析

格子名稱來自 Gamma API 每個 market 的 `groupItemTitle`,實際看過的格式只有這幾種:

| 原文 | 意思(整數結算值 v) |
|---|---|
| `57°F or below` / `18°C or below` | v ≤ 57 |
| `58-59°F` | 58 ≤ v ≤ 59 |
| `19°C` | v == 19 |
| `76°F or higher` / `28°C or higher` | v ≥ 76 |

解析成 `{"label": 原文, "lo": int|None, "hi": int|None}`(含端點;`None` = 無界)。遇到解析不了的格式要 raise,不要猜。

## 3. 資料來源(全部免金鑰,格式都實際打過)

**所有 HTTP 請求都要帶 `User-Agent: weatherLab/2 (+https://github.com/sssunwl/weatherLab)`**。Polymarket 會擋 Python 預設 UA(回 403)。timeout 20 秒,失敗重試 2 次。

### 3.1 Polymarket 市場:Gamma API

```
GET https://gamma-api.polymarket.com/events?slug=highest-temperature-in-{slug_city}-on-{month}-{day}-{year}
```
- `month` = 英文全名小寫(`september`),`day` 不補零(`9`),例:`highest-temperature-in-hong-kong-on-september-23-2026`
- 回傳 list;空 list = 這天還沒開市場(正常,跳過)
- `event["markets"]` 每個元素有:`groupItemTitle`(格子名)、`outcomePrices`(**JSON 字串**,例 `"[\"0.535\", \"0.465\"]"`,第 0 個是 YES)、`clobTokenIds`(**JSON 字串**,第 0 個是 YES token)、`closed`
- 市場已結算時,贏的那格 `outcomePrices[0]` 會是 `"1"`(> 0.99 就當贏)

### 3.2 Polymarket 價格:CLOB order book

```
GET https://clob.polymarket.com/book?token_id={YES token id}
```
回傳 `bids` / `asks` 兩個 list,元素 `{"price": "0.53", "size": "120"}`。
- `best_ask` = asks 裡 price 最小的;`best_bid` = bids 裡 price 最大的;沒有就 `null`
- `mid` = Gamma 的 `outcomePrices[0]`(轉 float)
- 紙上交易一律用 `best_ask` 買

### 3.3 預報:Open-Meteo Ensemble API

```
GET https://ensemble-api.open-meteo.com/v1/ensemble
  ?latitude={lat}&longitude={lon}
  &hourly=temperature_2m
  &models=ecmwf_ifs025,gfs025
  &timezone={tz}
  &forecast_days=3
  &temperature_unit={celsius|fahrenheit}   ← F 市場用 fahrenheit
```
- `hourly.time` 是當地時間字串(`2026-09-21T00:00`),前 10 字元就是當地日期
- `hourly` 裡除了 `time` 共 82 條序列:`temperature_2m_ecmwf_ifs025_ensemble`(控制組)、`temperature_2m_member01_ecmwf_ifs025_ensemble` … `member50`,以及 `temperature_2m_ncep_gefs025`、`temperature_2m_member01_ncep_gefs025` … `member30`。**不要寫死欄位名**:把所有 `temperature_2m` 開頭的鍵都當成一個成員(ECMWF 51 + GEFS 31 = 82)
- 每個成員:取目標日 24(或 23/25)個小時值的最大值 = 該成員的日最高溫。值有 `null` 就忽略該小時;某成員該日全是 null 就丟掉該成員

### 3.4 偏差校正用的歷史預報:Open-Meteo Historical Forecast API

```
GET https://historical-forecast-api.open-meteo.com/v1/forecast
  ?latitude={lat}&longitude={lon}
  &start_date=YYYY-MM-DD&end_date=YYYY-MM-DD
  &daily=temperature_2m_max
  &models=ecmwf_ifs025,gfs_seamless
  &timezone={tz}
  &temperature_unit={celsius|fahrenheit}
```
回傳 `daily.time`、`daily.temperature_2m_max_ecmwf_ifs025`、`daily.temperature_2m_max_gfs_seamless`。

### 3.5 實測:METAR(nyc / london / miami)— Iowa Environmental Mesonet

```
GET https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py
  ?station={iem_station}
  &data={tmpf|tmpc}                 ← F 市場 tmpf,C 市場 tmpc
  &year1=&month1=&day1=&year2=&month2=&day2=
  &tz={tz}
  &format=onlycomma&latlon=no&missing=M&trace=T
  &report_type=3&report_type=4      ← 兩個都要(例行+特別報),只給 3 倫敦會錯 6/25 天
```
- 回 CSV,欄位 `station,valid,tmpf`(或 `tmpc`);`valid` 是當地時間 `2026-09-20 14:51`,前 10 字元 = 當地日期
- `M` = 缺值,跳過
- end 日期要多抓一天(IEM 的 day2 不含當天)
- 結算值 = 該當地日所有數值的 max,再套 `settle_rule`

### 3.6 實測:香港天文台

**主要**(當月,每天更新):
```
GET https://www.hko.gov.hk/cis/dailyExtract/dailyExtract_{YYYYMM}.xml
```
名字叫 .xml 但內容是 JSON:`stn.data[0].dayData` 是 list,每列 `["01", " 997.6", "28.7", ...]`,**index 0 = 日(補零字串)、index 2 = 絕對最高氣溫**(字串,可能有空白,要 strip)。那天還沒發佈就是沒有那一列,或值不是數字 → 當成「還沒結算」。

**備援**(已定稿的月份):
```
GET https://data.weather.gov.hk/weatherAPI/opendata/opendata.php?dataType=CLMMAXT&station=HKO&year={YYYY}&rformat=json
```
`data` 每列 `["2026","8","9","36.9","C"]`(年、月、日、值、完整性)。當月通常是空的。

## 4. 機率怎麼算

對每個(市場, 目標日):

1. 從 3.3 拿到 N 個成員的日最高溫 `m_i`(單位跟市場一致)
2. 加偏差校正:`x_i = m_i - bias`(bias 見第 5 節;bias = 預報平均 − 實測平均,所以是減)
3. 每個成員當成一個常態分布 `Normal(x_i, kernel_sigma)`,算它落在每一格的機率,再對 N 個成員取平均:
   - 格子 `[lo, hi]` 在連續空間的範圍,依 `settle_rule`:
     - `round`:`[lo - 0.5, hi + 0.5)`
     - `floor`:`[lo, hi + 1)`
   - 無界的一端就用 ±∞
4. 各格機率加總應該 = 1(誤差 < 1e-6);用來顯示的機率夾在 `[0.001, 0.999]`,但 Brier 計算用原值
5. 同時記錄:`n_members`、成員日最高溫(校正後)的平均、10/50/90 百分位

## 5. 偏差校正 `data/bias.json`

- 每個市場用**最近 60 天**(不含今天)的「3.4 的 ECMWF 與 GFS 日最高溫平均」減「同一天的連續實測值」(**進位前**:METAR 的 max、天文台的小數值),取平均 = `bias`,同時算標準差 `resid_sd` 與有效天數 `n`
- 有效天數 < 20 天 → `bias = 0`,並在 Discord 訊息的尾巴寫「⚠️ {市場} 偏差樣本不足」
- 每天第一次跑(見第 8 節)重算一次就好
- 格式:`{"nyc": {"bias": 1.3, "resid_sd": 2.1, "n": 58, "window": ["2026-07-24","2026-09-21"], "updated": "..."}, ...}`

## 6. 紙上交易 `data/paper.json`

每次快照,對每一格:
- `edge = p - best_ask`(`best_ask` 為 null 就跳過)
- 下單條件**全部**成立:`edge >= 0.08`、`0.03 <= best_ask <= 0.85`、目標日的當地時間還沒開始(只交易「明天以後」的市場)、同一個(市場, 目標日, 格子)還沒下過單
- 每筆固定 $10,`shares = 10 / best_ask`
- 結算後:贏 → `pnl = shares - 10`,輸 → `pnl = -10`
- 這三個數字(0.08、0.03/0.85、$10)放在 `config/strategy.json`:`{"min_edge": 0.08, "min_ask": 0.03, "max_ask": 0.85, "stake": 10}`

每筆交易記:`id`、`market`、`target_date`、`bucket`、`p`、`best_ask`、`edge`、`shares`、`placed_at`(UTC ISO)、`status`(`open`/`won`/`lost`)、`pnl`。

## 7. 結算與評分

### 7.1 什麼時候可以結算
某(市場, 目標日)的當地日結束已超過 2 小時,且還沒結算 → 嘗試結算。回看最近 7 天。
- 拿不到實測(天文台還沒發佈、IEM 還沒資料)→ 維持 `pending`,下次再試。不算錯誤
- 同時抓 Gamma 看市場結果;市場還沒結算就先用我們自己的實測值

### 7.2 對帳
`data/results.json` 每筆:
```
{market, target_date, obs_raw, obs_value, our_bucket, market_bucket,
 eval_snapshot_at, probs: {label: p}, market_mids: {label: mid},
 p_of_actual, top_bucket, top_p, hit, brier, market_brier, settled_at}
```
- `obs_value` = `obs_raw` 套 `settle_rule`;`our_bucket` = 它落在哪一格
- `market_bucket` = Gamma 已結算時贏的那格,沒結算就 `null`,之後的 run 要補上
- `our_bucket != market_bucket`(兩個都不是 null 時)→ Discord 訊息要寫「⚠️ 結算不一致」,這代表我們對規則的理解有錯
- **評分用哪一次快照**:目標日當地 00:00 **之前**的最後一次快照(= 真的能下單的最後時刻)。沒有這種快照就不評分(`hit`、`brier` 為 null)
- `brier` = Σ(p_i − o_i)²,o 為實際那格 1、其他 0;`market_brier` 同公式,但用 `market_mids` 先除以總和正規化
- `hit` = 機率最高那格 == 實際那格

### 7.3 彙總 `data/summary.json`
- 每個市場 + 全部:最近 30 個已評分日的 `hit` 次數/天數、平均 `brier`、平均 `market_brier`
- 紙上交易:總筆數、勝率、累計 pnl、每日累計 pnl 序列(給曲線圖)
- 校準:把所有已評分日的所有格子依 `p` 分 10 箱(0–0.1 … 0.9–1.0),每箱記 `n`、平均 `p`、實際命中率

## 8. 執行流程與 CLI

一支入口:`python -m lab.run [--notify] [--now 2026-09-22T00:00:00Z]`
(`--now` 讓測試或補跑可以固定時間;沒給就用現在 UTC)

每次執行依序:
1. 讀設定。當天 UTC 日期第一次跑 → 重算 bias
2. **結算**(第 7 節)
3. **快照**:對每個 enabled 市場,目標日 = 當地今天、當地明天、當地後天。有市場(3.1 非空)的才做:抓格子 + 價格 + 算機率 + 紙上交易
4. 寫檔:
   - `data/snapshots/{UTC 日期}.jsonl`:每次快照每個(市場, 目標日)append 一行,含 `snapshot_at`、`market`、`target_date`、`bias`、成員統計、每格 `{label, lo, hi, p, best_bid, best_ask, mid}`
   - `data/latest.json`:本次快照(給儀表板「今天/明天」)
   - `data/results.json`、`data/paper.json`、`data/summary.json`、`data/bias.json`
5. `--notify` 才推 Discord(第 9 節)

程式碼放 `lab/` 套件,自己決定怎麼切檔,但要有 `lab/__init__.py`。任何單一市場失敗(網路錯、格式錯)不能讓整個 run 掛掉:記在該市場的 `errors`,其他市場照跑,Discord 訊息尾巴列出失敗的市場。整個 run 至少要寫出 `latest.json`。

## 9. Discord 每日訊息

用 `from scrapers.notify_discord import send`(需要的話在 `scrapers/` 加空的 `__init__.py`,這是唯一允許在 scrapers 裡新增的檔案)。整段包在一個 ``` code block 裡才會對齊。格式:

```
🌡 weatherLab 9/22
━ 昨日結算 ━
紐約 LGA   實測 72°F → 72-73°F  我們給 41% ✅ 最看好那格  市價 33%
倫敦 LCY   實測 20°C → 20°C     我們給 18% ❌ 最看好 21°C (38%)
香港 天文台 ⏳ 天文台未發佈
━ 明日機會(edge ≥ 8%)━
紐約 9/23 66-67°F  我們 61% / 賣價 53%  edge +8%  [模擬單]
━ 累計(紙上,最近 30 天)━
命中 12/30 · Brier 0.19(市價 0.21) · 模擬 18 筆 勝 7 · 損益 +$34
```
- 「昨日結算」= 這次 run 新結算的 + 還在 pending 的
- 沒有機會就寫「今天沒有值得下的」
- 評分天數 < 30 時累計那行照寫,但後面加「(樣本 n 天,還不能下結論)」
- 最後一行永遠是:`儀表板 https://sssunwl.github.io/weatherLab/`
- 錯誤、偏差樣本不足、結算不一致的警告放在儀表板連結那行之前

## 10. 儀表板 `index.html`(repo 根目錄)

GitHub Pages 已開,來源 = `main` 根目錄 → `https://sssunwl.github.io/weatherLab/`。

- `<meta name="robots" content="noindex,nofollow">`
- 單一檔案(CSS/JS 內嵌),`fetch('data/latest.json')` 等相對路徑讀資料,加 `?t=時間戳` 避免快取
- 繁體中文。手機寬度(375px)可讀,不能出現橫向捲動。跟隨系統淺色/深色
- 四個區塊,由上而下:
  1. **今天/明天每個市場**:每格一列,「我們的機率」與「市價 mid」兩條橫條並排,`edge ≥ 0.08` 的列標色;顯示快照時間與 bias
  2. **最近結算**:最近 7 天每個市場,實測值、落在哪格、我們給那格幾 %、✅/❌/⏳
  3. **校準圖**:inline SVG,x = 預測機率、y = 實際命中率,畫對角線,點的大小依 n;n < 30 時標註「樣本不足」
  4. **紙上交易累計損益**:inline SVG 折線
- 資料檔不存在或是空的時候,每個區塊顯示「還沒有資料」,不能整頁壞掉

## 11. GitHub Actions `.github/workflows/v2.yml`

```yaml
name: weatherLab v2
on:
  schedule:
    - cron: '0 0 * * *'    # 08:00 JST/HKT — 這次要推 Discord
    - cron: '0 12 * * *'   # 20:00 JST/HKT — 只快照不推
  workflow_dispatch:
    inputs:
      notify:
        type: boolean
        default: false
permissions:
  contents: write
concurrency:
  group: weatherlab-v2
  cancel-in-progress: false
```
- Python 3.11,`pip install requests`
- `--notify` 條件:`github.event.schedule == '0 0 * * *'` 或 `inputs.notify == true`
- 環境變數 `DISCORD_WEBHOOK_URL: ${{ secrets.DISCORD_WEBHOOK_URL }}`(Secret 已設好)
- 跑完 commit `data/`:沒變更就跳過;push 前 `git pull --rebase`,失敗重試 3 次;commit 者用 `github-actions[bot]`
- 同時刪除 `.github/workflows/scrape_weather.yml`

## 12. 測試 `tests/`(`python -m unittest discover tests`,不打網路)

至少涵蓋:
1. 格子解析:第 2.2 節四種格式各一例 + 解析不了會 raise
2. 進位:`round` 72.5→72-73°F 那格、71.4→70-71°F;`floor` 36.9→36°C、30.1→30°C、29.9→29°C;不能用 Python `round()`
3. 用這幾筆真實資料當 fixture,確認結算格子對:
   - 香港 2026-08-09 天文台 36.9 → `36°C`;08-21 31.5 → `31°C`;08-30 30.1 → `30°C`
   - 紐約 2026-09-20 METAR 最高 72.0°F → `72-73°F`;09-14 77.0 → `76-77°F`
   - 倫敦 2026-09-18 最高 21°C → `21°C`;2026-08-28 25°C,格子有 `25°C or higher` → 落在那格
4. 機率:一組假成員算出來各格加總 = 1;所有成員都在某格正中央、sigma 很小時,那格 > 0.95
5. 當地日邊界:倫敦 2026-10-25(夏令結束,25 小時)、紐約 2026-11-01 從 ensemble 小時資料切日正確
6. 結算時機:當地日結束未滿 2 小時不結算
7. 評分快照選擇:目標日當地 00:00 前的最後一次
8. Brier:完美預測 = 0;均勻分布 11 格時的值正確
9. 紙上交易:edge 門檻、ask 範圍、同格不重複下單、當地今天不下單、pnl 計算
10. Discord 訊息:給固定資料產生的文字符合第 9 節格式(至少檢查區段標題與儀表板連結)

## 13. 完成後回報(寫在你最後的訊息裡)

1. 新增/修改/刪除的檔案清單
2. `python -m unittest discover tests` 的完整輸出
3. 有沒有實際跑過 `python -m lab.run --now ...`(不加 `--notify`)?成功的話附 `data/latest.json` 的前 40 行
4. 哪些 API 沒實際打過
5. 本文件沒講清楚、你自己做了決定的地方(逐條列)
