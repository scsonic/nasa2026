# Earth Spins — A NASA Data Jukebox (nasa2026pre)

NASA Space Apps 2026 挑戰題目：[The Earth Information Jukebox](https://www.spaceappschallenge.org/2026/challenges/the-earth-information-jukebox/)

選一個 NASA 地球視覺化 → Gemini 看圖 + 讀說明 → 產生兩首約 30 秒的配樂（Google Lyria 2、Meta MusicGen）→ 可播放、可用 5 碼短網址分享。

## 流程

```
瀏覽器（1920x1080 舞台：左 60% jukebox、右 40% 黑板）
  │  轉主題轉盤（◀ ▶、點選項、滾輪或左右滑），停在中間的主題就搜尋
  ▼
GET /api/search?q=&page= ──► NASA SVS（EIC 結果在前、一般 SVS 在後；每層樓 6 張名片，▲▼ 換樓）
  │  點名片 → 5 位數號碼盤變成該 SVS 編號、碟片區抽出那張唱片（或點號碼盤／直接按數字鍵，跳出輸入框輸入 1–5 位 SVS 編號）
  │  或按 RANDOM：隨機挑一個 SVS 編號（找不到就換一個，最多 8 次）並直接開始
  ▼  按 START
POST /api/generate ──► 建立 5 碼 share code，寫入 SQLite，背景開始：
  1. fetching   讀 SVS /api/<id>（標題、說明、圖說、主圖、影片）
  2. watching   有 mp4 時，用 ffmpeg 從影片前 30 秒抓 6 張（0/6/12/18/24 秒 + 第 30 秒，
                縮到 632px 以內）；沒有 mp4 就跳過（約多 35 秒）
  3. analyzing  Gemini 3.8 Flash：主圖 + 6 張影片畫面 + 文字 → 結構化 JSON（影片分析、情緒、BPM、
                調性、樂器、六段式音樂規劃、Lyria prompt、MusicGen prompt）
  4. composing  Lyria 2 (Vertex AI) 產生 ~33 秒 → 轉 MP3 → 播放器可播
     同時       MusicGen 在 Colab GPU 背景產生（warm session，閒置 15 分自動關機；一次一首，排隊處理）
GET /api/generations/<code> ──► 進度條輪詢（每 1.5 秒）；Part 1 (Google) 播完自動接 Part 2 (MusicGen)
/share/<code> ──► 任何人打開都來到同一台 jukebox 並直接播放（瀏覽器擋自動播放時顯示 Press to play）
```

SVS 網頁設了 `X-Frame-Options: DENY`，無法 iframe，所以右邊黑板是用 SVS API + Gemini 分析自己寫的（摘要、怎麼聽、畫面→聲音、兩段作曲 prompt）。點黑板上的照片會開 NASA 原頁。

RANDOM 的編號範圍（2026-11 抽樣 NASA SVS）：`1–5690`（1998 至今，含最新的 Hyperwall/EIC）與 `10000–15120`（2008 至今的主要動畫），兩段約 75% 是真的頁面；`6000–9999` 和 `15120` 以上幾乎是空號，所以不抽。範圍定義在 `static/jukebox.js` 的 `RANDOM_RANGES`。

分享鈕的小窗：目前這首的連結 + 你在這個瀏覽器按過 START 的所有記錄（存在 cookie `jbx_plays`，只存 code/編號/時間，標題向伺服器查）。同一個 SVS 編號可以產生很多次，各有自己的時間與連結。

## 本機執行

需求：`uv`、`gcloud`、`colab` CLI（`uv tool install google-colab-cli`）、`ffmpeg` + `ffprobe`（影片抓圖與轉 MP3；沒有的話會跳過影片分析）。

Google 登入（Vertex AI 和 Colab 共用同一個 ADC 登入，`.env` 設 `COLAB_AUTH=adc`）：

```bash
gcloud auth application-default login --no-launch-browser \
  --scopes=openid,https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/userinfo.email,https://www.googleapis.com/auth/colaboratory
gcloud auth application-default set-quota-project team-bubu
```

`colaboratory` scope 一定要在登入時就給（gcloud 的使用者憑證事後不能加 scope）。登入會在 `~/.config/gcloud/application_default_credentials.json` 存 refresh token，不會固定過期；除非被撤銷、6 個月沒用，或 Workspace 管理員設了 session 時效。`COLAB_AUTH=oauth2` 會把 token 存在系統 keyring，headless 的伺服器（systemd）解不開，所以伺服器上要用 `adc`。

```bash
cd nasa2026pre
cp .env.example .env        # 已建立；本機預設值即可用
uv sync
uv run uvicorn app.main:app --port 8000
# 開 http://localhost:8000
```

- 不想動用 Colab：`MUSICGEN_ENABLED=false uv run uvicorn app.main:app --port 8000`
- 開發時用 `--reload` 會在每次重載時關掉 Colab session（安全但會重開機），測 MusicGen 時建議不要加。

## 金鑰與費用

- **不需要任何 API key**。本機用 gcloud ADC 呼叫 `team-bubu` 的 Vertex AI（Gemini、Lyria 已啟用、billing 已開）。上雲端改用 Cloud Run 的 service account（給 `roles/aiplatform.user`）。
- Lyria 2：每 30 秒 $0.06（每次約 33 秒），加 Gemini 約 $0.07／次 → $10/月 約 140 次。防濫用：同一個 IP 30 分鐘內最多 30 次（`RATE_LIMIT_COUNT` / `RATE_LIMIT_WINDOW_MINUTES`），超過回 429，畫面跳出「Too many plays」。部署在 nginx 後面時，nginx 用 `deploy/nginx/cloudflare-realip.conf` 從 `CF-Connecting-IP` 還原真實 IP 並放進 `X-Forwarded-For`，uvicorn 預設就信任 127.0.0.1 送來的這個 header。
- Colab T4：1.15 CU/小時。冷啟動（開機＋下載模型）約 2.5 分鐘，但有些 VM（例如分到歐洲機房）載入模型要 9 分鐘；之後每首 30 秒約 75–80 秒。餘額與用量：`colab --auth=adc usage`。

## MusicGen 的檢查與錯誤訊息

- 單首生成上限 `MUSICGEN_TIMEOUT_SECONDS`（預設 300 秒；正常約 80 秒），超過就停止並標成失敗，換下一首。
- colab CLI 斷線時會印出 `Connection was lost` 卻不結束；現在看到就立刻停止，並重試一次（Colab 上已經有檔案就直接下載）。
- 遠端腳本印出 `MG_READY` / `MG_DONE` 就當作完成，不等 kernel 的完成回覆。
- 失敗時播放器顯示「Part 2 failed · <原因>」：超時、被中止、斷線、Colab 上出錯、session 結束、伺服器沒有 colab CLI 各有一句話；技術細節寫在 log。
- `/api/health` 的 `musicgen` 有 `last_generate_seconds`、`timeout_seconds`、`connection_lost`、`timeouts` 可以檢查。

實測（本機）：

| | 冷啟動 | warm |
|---|---|---|
| Lyria 可播放 | ~40–85 秒 | ~40–50 秒 |
| MusicGen 可播放 | ~4.5 分 | ~1.7 分 |

## API

| Method | Path | 說明 |
|---|---|---|
| GET | `/api/categories` | 選擇器上的主題清單（`app/categories.py`） |
| GET | `/api/search?q=&page=0` | SVS 搜尋，每頁 6 筆（EIC 優先）；`q` 空字串 = 最新 EIC |
| GET | `/api/cover/{id}` | 轉址到該 SVS 頁的主圖（名片、唱片封面用） |
| GET | `/api/generations?codes=a,b` | 分享小窗的歷史清單 |
| GET | `/api/svs/{id}` | 整理過的 SVS 單筆資料 |
| POST | `/api/generate` `{svs_id, search_term}` | 開始產生，回 `{code, share_url}` |
| GET | `/api/generations/{code}` | 狀態、進度、分析、兩條音軌 |
| GET | `/api/generations/{code}/record` | 這次產生完整存檔的 JSON |
| GET | `/api/recent` | 最近的作品 |
| GET | `/api/health` | 設定、Colab session 狀態、最近一次生成秒數與失敗次數 |
| GET | `/share/{code}` | 分享頁 |

## 檔案

```
app/main.py           FastAPI 路由、生命週期（啟動時把中斷的工作標成 failed、關機時停 Colab）
app/jobs.py           產生流程與進度估算
app/svs.py            NASA SVS API（搜尋、單筆、抓圖給 Gemini）
app/analyze.py        Gemini 圖文分析（結構化輸出）
app/lyria.py          Lyria 2 (Vertex AI predict)
app/musicgen_colab.py Colab warm session 管理（new / exec / download / idle stop、超時與斷線處理）
colab/*.py            在 Colab kernel 裡執行的 MusicGen 載入與生成腳本
app/db.py             SQLite（data/jukebox.db）
static/               jukebox 前端（純 HTML/CSS/JS，不用 build）
data/                 資料庫、音檔（gitignore）
deploy/               systemd unit 與 nginx 設定（正式機用的版本）
```

## 正式部署（nasa2026.scsonic.com）

```
Browser --HTTPS--> Cloudflare（橘色雲朵、SSL 模式 Flexible）--HTTP:80--> nginx --> uvicorn 127.0.0.1:8200
```

- **主機**：Ubuntu ARM VM，Python 用 miniconda `base`（`pip install` 專案的相依套件、`google-colab-cli`），另外裝 `ffmpeg`，`gcloud` 只拿來登入。
- **systemd**：`deploy/nasa2026.service` → `/etc/systemd/system/`。只開 1 個 worker（工作和 Colab worker 都在同一個 process 裡），只聽 127.0.0.1。log 在 `/var/log/nasa2026/{access,error}.log`（要先 `mkdir` 並 `chown ubuntu`）。
- **nginx**：`deploy/nginx/nasa2026.scsonic.com.conf` 與 `cloudflare-realip.conf`（放 `/etc/nginx/snippets/`）。Flexible 模式下 Cloudflare 用 HTTP 連進來，所以 80 port **不能**轉址到 https，會無限轉址。
- **Cloudflare DNS 要保持橘色雲朵**：關掉的話瀏覽器會直接連 443，nginx 沒有 nasa2026 的 443 設定，會跑到 `scsonic.com` 預設站。
- **`.env`**：`PUBLIC_BASE_URL=https://nasa2026.scsonic.com`（分享連結用）、`COLAB_AUTH=adc`。
- **重開服務**會停掉 Colab session，正在跑的工作會標成「server restarted before this finished」，下一首 MusicGen 要冷啟動。最好等 `/api/health` 的 `musicgen.queue` 是 0 再重開。

## 若改上 Cloud Run

1. **SQLite 與音檔的保存**：Cloud Run 的檔案系統重啟就消失。選項：掛 GCS（Cloud Storage FUSE）放 `data/`，或資料庫改 Firestore、音檔放 GCS。
2. **MusicGen 在雲端怎麼跑**：Colab 需要使用者身分的憑證（ADC），要當 secret 帶上去；或 MusicGen 只在自己的機器跑，雲端只開 Lyria。
3. **ffmpeg**：Cloud Run 的 image 要裝 ffmpeg（影片分析需要）。
