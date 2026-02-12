# PDF構造化システム コンポーネント図

```mermaid
graph TB
    subgraph Docker["Docker Compose"]
        subgraph UI["Streamlit UI :8501"]
            UI_Admin["Admin画面<br/>PDF管理"]
            UI_User["User画面<br/>チャット"]
        end

        subgraph API["FastAPI Backend :8000"]
            API_PDF["pdf_processor.py<br/>PDF → 構造化JSON"]
            API_Chat["chat_service.py<br/>AI チャット処理"]
            API_Agent["agent_config.py<br/>LLMクライアント"]
        end

        subgraph LiteLLM["LiteLLM Proxy :4000"]
            LiteLLM_Core["LLMプロバイダー統合"]
            LiteLLM_Azure["Azure OpenAI"]
            LiteLLM_OpenAI["OpenAI"]
            LiteLLM_Bedrock["Bedrock"]
        end

        subgraph MCP["FastMCP Server :8001"]
            MCP_Search["search_documents<br/>点検記録検索"]
            MCP_Viz["visualize_data<br/>グラフ生成"]
            MCP_Chart["chart_utils.py<br/>matplotlib 散布図"]
        end

        subgraph Mongo["MongoDB :27017"]
            Mongo_Pages["pages コレクション<br/>構造化データ"]
        end

        subgraph Volume["/data 共有ボリューム"]
            Vol_Raw["raw/ - 元PDF"]
            Vol_Img["images/ - ページ画像"]
            Vol_Chart["charts/ - グラフ画像"]
        end
    end

    ExtLLM["外部LLM API<br/>Azure / OpenAI / AWS"]

    %% UI → API
    UI_Admin -->|"HTTP POST<br/>/admin/files, /admin/process"| API_PDF
    UI_User -->|"HTTP POST<br/>/chat"| API_Chat

    %% API → LiteLLM
    API_PDF -->|"OpenAI互換API<br/>GPT-4o Vision"| LiteLLM_Core
    API_Agent -->|"OpenAI互換API"| LiteLLM_Core

    %% API → MCP
    API_Chat -->|"MCPプロトコル<br/>HTTP Streamable"| MCP_Search
    API_Chat -->|"MCPプロトコル<br/>HTTP Streamable"| MCP_Viz

    %% MCP内部
    MCP_Viz --> MCP_Chart

    %% → MongoDB
    API_PDF -->|"motor async"| Mongo_Pages
    MCP_Search -->|"$regex 検索"| Mongo_Pages
    MCP_Viz -->|"$regex 検索"| Mongo_Pages

    %% LiteLLM → 外部
    LiteLLM_Core -->|"HTTPS"| ExtLLM

    %% Volume接続
    API_PDF -.->|"保存"| Volume
    MCP_Chart -.->|"保存"| Vol_Chart
    UI_User -.->|"読み込み"| Volume

    style UI fill:#E3F2FD,stroke:#1565C0
    style API fill:#E8F5E9,stroke:#2E7D32
    style LiteLLM fill:#FFF3E0,stroke:#E65100
    style MCP fill:#F3E5F5,stroke:#6A1B9A
    style Mongo fill:#E0F2F1,stroke:#00695C
    style Volume fill:#FFF9C4,stroke:#F57F17
    style ExtLLM fill:#FFEBEE,stroke:#C62828
```

## 通信一覧

| 送信元 | 送信先 | プロトコル | 用途 |
|--------|--------|-----------|------|
| Streamlit UI | FastAPI | HTTP REST | PDF管理、チャット |
| FastAPI | LiteLLM | HTTP (OpenAI互換) | LLM呼び出し |
| FastAPI | FastMCP | HTTP (MCP) | ツール実行 |
| FastAPI | MongoDB | TCP (motor) | データ保存 |
| FastMCP | MongoDB | TCP (motor) | データ検索 |
| LiteLLM | 外部LLM | HTTPS | AI推論 |

## 共有ボリューム `/data`

| パス | 用途 | 書き込み | 読み込み |
|------|------|---------|---------|
| `/data/{tenant}/raw/` | 元PDFファイル | API | API |
| `/data/{tenant}/images/` | PDFページ画像 | API | UI, MCP |
| `/data/charts/` | グラフ画像 | MCP | UI |
