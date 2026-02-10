# 開發代理指南 (Agent Guidelines)

## 核心原則

### 1. 📋 查看計畫 (Check Plans First)

在開始任何開發工作之前，**必須**查看相關的計畫文件：

- [`plans/architecture-v2.md`](plans/architecture-v2.md) - 系統架構設計
- [`plans/architecture.md`](plans/architecture.md) - 初始架構參考
- [`plans/quick-start-guide.md`](plans/quick-start-guide.md) - 快速開始指南
- [`plans/tts-voice-config.md`](plans/tts-voice-config.md) - TTS 語音配置

**為什麼？** 計畫文件定義了系統的整體設計、API 端點、數據流和技術棧。在開發前查看計畫可以確保：
- 避免重複工作
- 保持架構一致性
- 理解業務邏輯和用戶流程
- 減少返工

---

### 2. 🔄 SSE 流實現參考

當實現流式 API 響應時，參考 [`doc/poe-SSE.txt`](doc/poe-SSE.txt) 中的 Server-Sent Events (SSE) 格式。

**SSE 流的基本結構：**

```json
{
  "id": "chatcmpl-xxxxx",
  "object": "chat.completion.chunk",
  "created": 1770752590,
  "model": "model-name",
  "choices": [
    {
      "index": 0,
      "delta": {
        "role": "assistant",
        "content": "流式內容"
      },
      "finish_reason": null
    }
  ]
}

// 流結束時
{
  "id": "chatcmpl-xxxxx",
  "object": "chat.completion.chunk",
  "created": 1770752590,
  "model": "model-name",
  "choices": [
    {
      "index": 0,
      "delta": {},
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "completion_tokens": 1305,
    "prompt_tokens": 6,
    "total_tokens": 1311
  }
}

[DONE]
```

**實現要點：**
- 每個 chunk 包含 `delta` 欄位，其中包含增量內容
- 使用 `finish_reason` 標記流的狀態（`null` 表示繼續，`"stop"` 表示結束）
- 流結束時發送 `[DONE]` 標記
- 包含 `usage` 統計信息（可選但推薦）

---

### 3. ⚙️ API 配置管理

**所有 API 配置都必須在 `.env.example` 中定義，不要硬編碼在代碼中。**

#### 配置文件結構

參考 [`.env.example`](.env.example)：

```env
# LLM API (OpenAI 相容)
LLM_API_KEY="your_LLM_API_key_here"
LLM_API_BASE_URL="https://api.openai.com/v1"

# POE API
IMAGE_API_KEY="your_poe_token_here"
IMAGE_API_URL="https://api.poe.com/v1/chat/completions"

# TTS API
TTS_API_URL=""

# 應用程式設定
BACKEND_PORT=8000
```

#### 使用配置的方式

**Python (FastAPI 後端)：**

```python
import os
from dotenv import load_dotenv

load_dotenv()

LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_API_BASE_URL = os.getenv("LLM_API_BASE_URL", "https://api.openai.com/v1")
IMAGE_API_KEY = os.getenv("IMAGE_API_KEY")
IMAGE_API_URL = os.getenv("IMAGE_API_URL")
TTS_API_URL = os.getenv("TTS_API_URL", "")
BACKEND_PORT = int(os.getenv("BACKEND_PORT", 8000))
```

**JavaScript/Vue (前端)：**

```javascript
// 從環境變數讀取配置
const API_BASE_URL = process.env.VUE_APP_API_BASE_URL || 'http://localhost:8000'
const IMAGE_API_URL = process.env.VUE_APP_IMAGE_API_URL
```

#### 禁止硬編碼

❌ **不要這樣做：**

```python
# 硬編碼 API URL - 禁止！
response = requests.post("https://api.openai.com/v1/chat/completions", ...)
```

✅ **應該這樣做：**

```python
# 從環境變數讀取
api_url = os.getenv("LLM_API_BASE_URL")
response = requests.post(f"{api_url}/chat/completions", ...)
```

---

### 4. 🔗 API URL 自訂性

**所有 API 都必須允許自訂 URL，不要假設默認值。**

#### 為什麼需要自訂 URL？

- 用戶可能使用自建的 API 服務
- 可能需要使用代理或中間層
- 不同環境（開發、測試、生產）可能有不同的 API 端點
- 支持本地開發和遠程服務的切換

#### 實現方式

1. **在 `.env.example` 中定義所有 API URL**
2. **在代碼中讀取環境變數，提供合理的默認值**
3. **在 API 調用時使用配置的 URL，而不是硬編碼**

**示例：**

```python
# config.py
import os

class APIConfig:
    # LLM API
    LLM_API_KEY = os.getenv("LLM_API_KEY")
    LLM_API_BASE_URL = os.getenv("LLM_API_BASE_URL", "https://api.openai.com/v1")
    
    # POE API
    IMAGE_API_KEY = os.getenv("IMAGE_API_KEY")
    IMAGE_API_URL = os.getenv("IMAGE_API_URL", "https://api.poe.com/v1/chat/completions")
    
    # TTS API
    TTS_API_URL = os.getenv("TTS_API_URL", "")  # 空字符串表示使用內置 edge-tts

# 在 API 調用中使用
async def call_LLM_API(prompt: str):
    headers = {
        "Authorization": f"Bearer {APIConfig.LLM_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # 使用配置的 URL，而不是硬編碼
    url = f"{APIConfig.LLM_API_BASE_URL}/chat/completions"
    
    response = await httpx.post(url, json={...}, headers=headers)
    return response
```

---

### 5. 🎯 後端優先，前端選擇

**當實現功能時，如果有多種選擇或不確定具體實現方式，後端應該提供所有選項，讓前端決定使用哪一個。**

#### 原則

- **後端責任**：提供靈活的 API，支持多種選項和配置
- **前端責任**：根據用戶需求選擇合適的選項
- **通信方式**：通過 API 參數和響應數據進行選擇

#### 實現示例

**場景：視頻分段方式有多種選擇**

根據 [`plans/architecture-v2.md`](plans/architecture-v2.md:100-105)，分段策略有三種：

```python
# 後端 API 支持所有分段方式
@app.post("/api/segment-text")
async def segment_text(
    text: str,
    method: str = "smart"  # "sentence" | "fixed" | "smart"
):
    """
    支持多種分段方式，讓前端選擇
    """
    if method == "sentence":
        segments = segment_by_sentence(text)
    elif method == "fixed":
        segments = segment_by_fixed_length(text, length=100)
    elif method == "smart":
        segments = segment_by_smart_detection(text)
    else:
        raise ValueError(f"Unknown method: {method}")
    
    return {"segments": segments}
```

**前端根據用戶選擇調用：**

```javascript
// 前端讓用戶選擇分段方式
const segmentMethod = userSelection; // "sentence" | "fixed" | "smart"

const response = await fetch('/api/segment-text', {
  method: 'POST',
  body: JSON.stringify({
    text: novelText,
    method: segmentMethod
  })
});
```

#### 另一個例子：字幕樣式

根據 [`plans/architecture-v2.md`](plans/architecture-v2.md:222)，字幕有多種樣式：

```python
# 後端支持所有字幕樣式
@app.post("/api/generate-video")
async def generate_video(
    text: str,
    subtitle_style: str = "highlight"  # "basic" | "highlight" | "danmaku" | "center"
):
    """
    後端支持所有字幕樣式，前端選擇
    """
    # 後端生成視頻時應用選定的字幕樣式
    video = create_video(text, subtitle_style=subtitle_style)
    return video
```

---

### 6. 🚫 API 預設使用非流式模式

**API 預設應該使用非流式（非 SSE）模式，避免額外的報錯和複雜性。**

#### 為什麼？

- 非流式模式更簡單、更穩定
- 避免流式傳輸中的連接中斷、超時等問題
- 對於大多數場景，非流式模式已經足夠
- 流式模式應該是可選的高級功能，而不是默認行為

#### 實現方式

**後端 API 設計：**

```python
# 默認使用非流式模式
@app.post("/api/analyze-characters")
async def analyze_characters(
    text: str,
    stream: bool = False  # 默認 False，使用非流式
):
    """
    分析小說角色
    
    Args:
        text: 小說文本
        stream: 是否使用流式模式（可選）
    
    Returns:
        如果 stream=False，返回完整的 JSON 響應
        如果 stream=True，返回 SSE 流
    """
    
    if stream:
        # 流式模式：返回 SSE 流
        return StreamingResponse(
            stream_analyze_characters(text),
            media_type="text/event-stream"
        )
    else:
        # 非流式模式：返回完整響應（默認）
        result = await llm_api.analyze(text)
        return {
            "characters": result.characters,
            "confidence": result.confidence
        }
```

**前端調用：**

```javascript
// 默認使用非流式模式（推薦）
const response = await fetch('/api/analyze-characters', {
  method: 'POST',
  body: JSON.stringify({
    text: novelText
    // stream 參數省略，默認為 false
  })
});

const data = await response.json();
console.log(data.characters);

// 如果需要流式模式，顯式設置 stream=true
const streamResponse = await fetch('/api/analyze-characters', {
  method: 'POST',
  body: JSON.stringify({
    text: novelText,
    stream: true  // 顯式啟用流式模式
  })
});

// 處理 SSE 流
const reader = streamResponse.body.getReader();
// ... 處理流式數據
```

#### 流式模式的使用場景

只在以下情況下使用流式模式：
- 需要實時顯示長時間運行操作的進度
- 需要逐步返回大量數據
- 用戶體驗需要實時反饋

**示例：長時間運行的視頻生成**

```python
@app.post("/api/generate-video")
async def generate_video(
    text: str,
    stream: bool = False
):
    """
    生成視頻
    
    Args:
        stream: 是否使用流式模式返回進度
    """
    
    if stream:
        # 流式模式：實時返回進度
        return StreamingResponse(
            stream_video_generation(text),
            media_type="text/event-stream"
        )
    else:
        # 非流式模式：等待完成後返回結果（默認）
        video_path = await generate_video_internal(text)
        return {
            "video_path": video_path,
            "status": "completed"
        }
```

---

## 開發工作流程

### 開始新功能開發時

1. ✅ **查看計畫** - 閱讀相關的 `plans/*.md` 文件
2. ✅ **檢查 API 設計** - 確認 API 端點和參數
3. ✅ **檢查配置** - 確認 `.env.example` 中是否有必要的配置
4. ✅ **實現後端** - 提供靈活的 API，支持多種選項
5. ✅ **實現前端** - 根據後端 API 提供的選項讓用戶選擇
6. ✅ **測試** - 確保所有配置都可以正確讀取和使用

### 代碼審查檢查清單

- [ ] 是否查看了相關的計畫文件？
- [ ] 是否有硬編碼的 API URL 或密鑰？
- [ ] 所有 API 配置是否都在 `.env.example` 中定義？
- [ ] 是否支持自訂 API URL？
- [ ] 後端是否提供了足夠的選項讓前端選擇？
- [ ] API 是否默認使用非流式模式？
- [ ] 流式模式是否是可選的？

---

## 參考文件

- 📄 [`plans/architecture-v2.md`](plans/architecture-v2.md) - 系統架構設計
- 📄 [`doc/poe-SSE.txt`](doc/poe-SSE.txt) - SSE 流實現示例
- 📄 [`.env.example`](.env.example) - 環境變數配置模板