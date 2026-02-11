# GenVideo

小說推文視頻生成系統（前後端完整實作）：
- 動態模型列表
- AI 角色分析與建議音色
- 文本分段（句子/固定/智能）
- 圖片 + TTS + 字幕合成視頻
- 任務狀態查詢與下載

## 目錄

- `backend/` FastAPI + MoviePy
- `frontend/` Vue 3 + Element Plus + Vite
- `plans/` 規劃文件

## 環境準備

1. 安裝 Python 3.11+
2. 安裝 Node.js 18+
3. 安裝 ffmpeg（MoviePy 需要）

## 配置

複製 `.env.example` 為 `.env` 或使用 `.env.local`。

建議至少配置：

- `LLM_API_KEY`
- `LLM_API_BASE_URL`
- `IMAGE_API_KEY`
- `IMAGE_API_URL`

可選：

- `TTS_API_URL`（留空時走 `edge-tts`）
- `LLM_DEFAULT_MODEL`
- `IMAGE_MODEL`

## 啟動後端

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

後端默認啟動在 `http://localhost:8000`

## 啟動前端

```bash
cd frontend
npm install
npm run dev
```

前端默認啟動在 `http://localhost:5173`

## 主要 API

- `GET /api/models`
- `POST /api/analyze-characters`
- `POST /api/confirm-characters`
- `POST /api/segment-text`
- `POST /api/generate-video`
- `GET /api/jobs/{job_id}`
- `GET /api/jobs/{job_id}/video`

## 備註

- 若圖片 API/TTS API 不可用，系統會自動使用降級方案（佔位圖/靜音音頻）確保流程可跑通。
- 任務產物輸出在 `outputs/`。

