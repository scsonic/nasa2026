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
  ▼  按 START
POST /api/generate ──► 建立 5 碼 share code，寫入 SQLite，背景開始：
  1. fetching   讀 SVS /api/<id>（標題、說明、圖說、主圖、影片）
  2. watching   有 mp4 時，用 ffmpeg 從影片前 30 秒抓 6 張（0/6/12/18/24 秒 + 第 30 秒，
                縮到 632px 以內）；沒有 mp4 就跳過（約多 35 秒）
  3. analyzing  Gemini 3.8 Flash：主圖 + 6 張影片畫面 + 文字 → 結構化 JSON（影片分析、情緒、BPM、
                調性、樂器、六段式音樂規劃、Lyria prompt、MusicGen prompt）
  4. composing  Lyria 2 (Vertex AI) 產生 ~33 秒 → 轉 MP3 → 播放器可播
     同時       MusicGen 在 Colab GPU 背景產生（warm session，閒置 15 分自動關機）
GET /api/generations/<code> ──► 進度條輪詢（每 1.5 秒）；Part 1 (Google) 播完自動接 Part 2 (MusicGen)
/share/<code> ──► 任何人打開都來到同一台 jukebox 並直接播放（瀏覽器擋自動播放時顯示 Press to play）
```

SVS 網頁設了 `X-Frame-Options: DENY`，無法 iframe，所以右邊黑板是用 SVS API + Gemini 分析自己寫的（摘要、怎麼聽、畫面→聲音、兩段作曲 prompt）。點黑板上的照片會開 NASA 原頁。

分享鈕的小窗：目前這首的連結 + 你在這個瀏覽器按過 START 的所有記錄（存在 cookie `jbx_plays`，只存 code/編號/時間，標題向伺服器查）。同一個 SVS 編號可以產生很多次，各有自己的時間與連結。

## 本機執行

需求：`uv`、`gcloud`（已 `gcloud auth application-default login`）、`colab` CLI（已登入 oauth2）、`ffmpeg` + `ffprobe`（影片抓圖與轉 MP3；沒有的話會跳過影片分析）。

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
- Lyria 2：每 30 秒 $0.06（每次約 33 秒），加 Gemini 約 $0.07／次 → $10/月 約 140 次。防濫用：同一個 IP 30 分鐘內最多 30 次（`RATE_LIMIT_COUNT` / `RATE_LIMIT_WINDOW_MINUTES`），超過回 429，畫面跳出「Too many plays」。部署在 proxy 後面要用 `uvicorn --proxy-headers --forwarded-allow-ips=*` 才拿得到真實 IP。
- Colab T4：1.15 CU/小時。冷啟動（開機＋下載模型）約 2.5 分鐘，之後每首約 80 秒。

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
| GET | `/api/health` | 設定與 Colab session 狀態 |
| GET | `/share/{code}` | 分享頁 |

## 檔案

```
app/main.py           FastAPI 路由、生命週期（啟動時把中斷的工作標成 failed、關機時停 Colab）
app/jobs.py           產生流程與進度估算
app/svs.py            NASA SVS API（搜尋、單筆、抓圖給 Gemini）
app/analyze.py        Gemini 圖文分析（結構化輸出）
app/lyria.py          Lyria 2 (Vertex AI predict)
app/musicgen_colab.py Colab warm session 管理（new / exec / download / idle stop）
colab/*.py            在 Colab kernel 裡執行的 MusicGen 載入與生成腳本
app/db.py             SQLite（data/jukebox.db）
static/               jukebox 前端（純 HTML/CSS/JS，不用 build）
data/                 資料庫、音檔（gitignore）
```

## 部署前要決定的事

1. **SQLite 與音檔的保存**：Cloud Run 的檔案系統重啟就消失。選項：掛 GCS（Cloud Storage FUSE）放 `data/`，或資料庫改 Firestore、音檔放 GCS。
2. **MusicGen 在雲端怎麼跑**：Colab CLI 用的是你個人的 OAuth token，放上 Cloud Run 要把 token 當 secret 帶上去。另一個選項是 MusicGen 只在本機/自己的機器跑，雲端只開 Lyria。
3. **ffmpeg**：Cloud Run 的 image 要裝 ffmpeg（影片分析需要）。
4. **網域**：`nasa2026.scsonic.com` 指到 Cloud Run 後，把 `.env` 的 `PUBLIC_BASE_URL` 改成 `https://nasa2026.scsonic.com`。
