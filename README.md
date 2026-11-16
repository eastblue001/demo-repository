# Yahoo Finance MCP Server

Yahoo Finance の市場データを MCP (Model Context Protocol) で公開する、Azure Container Apps 対応の Python サーバーです。MCP の Streamable HTTP エンドポイントは `/mcp` です。

## 提供ツール

| MCP tool | 内容 |
|---|---|
| `get_quote` | 最新価格、前日終値、日中・52週レンジ、時価総額など |
| `get_history` | OHLCV 履歴。period または start/end、interval、調整価格に対応 |
| `get_company_profile` | 企業概要、セクター、業種、主要バリュエーション |
| `get_financial_statement` | 損益計算書、貸借対照表、キャッシュフロー（年次・四半期） |
| `get_news` | 銘柄に紐づく最近のニュース |
| `get_options` | 満期一覧とコール／プットのオプションチェーン |
| `search_tickers` | 企業名・資産名から Yahoo Finance のシンボルを検索 |

日本株は `7203.T`、日経平均は `^N225`、為替は `USDJPY=X` のような Yahoo Finance シンボルを指定します。

## 前提

- Python 3.11 以上（コンテナは Python 3.12）
- Docker（ローカルコンテナ実行時）
- Azure CLI、Bicep、Azure サブスクリプション（Azure へデプロイする場合）

## ローカル実行

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
python -m yahoo_finance_mcp.server
```

エンドポイント: `http://localhost:8000/mcp`

MCP Inspector を使う例:

```bash
npx @modelcontextprotocol/inspector http://localhost:8000/mcp
```

## Docker

```bash
docker build -t yahoo-finance-mcp:local .
docker run --rm -p 8000:8000 --env-file .env.example yahoo-finance-mcp:local
```

コンテナは非 root ユーザーで稼働し、`8000/tcp` のソケットヘルスチェックを持ちます。

## テスト

テストは Yahoo Finance への外部通信をモックしているため、安定して実行できます。

```bash
pip install -e '.[dev]'
pytest
ruff check src tests
ruff format --check src tests
```

## Azure Container Apps へのデプロイ

スクリプトはリソースグループと ACR を作成（既存なら再利用）し、ACR Tasks でイメージをビルドして Bicep をデプロイします。ACR 名はグローバルに一意である必要があります。

```bash
az login
ACR_NAME=<globally-unique-acr-name> ./deploy/deploy.sh
```

既定リージョンは `japaneast` です。変更例:

```bash
LOCATION=japanwest \
RESOURCE_GROUP=rg-market-data \
APP_NAME=yahoo-finance-mcp \
ACR_NAME=<globally-unique-acr-name> \
./deploy/deploy.sh
```

コマンドの最後に次の形式の MCP URL が出力されます。

```text
https://<container-app-fqdn>/mcp
```

Bicep は以下を構成します。

- Azure Container Registry からの pull にユーザー割り当てマネージド ID を使用
- HTTPS の外部 ingress、コンテナ target port `8000`
- ステートレス HTTP（複数レプリカ対応）
- TCP liveness/readiness probe
- 0.5 vCPU / 1 GiB、1～3 レプリカ、HTTP 同時実行数によるスケール

プライベート利用にする場合は `deploy/main.bicep` の `externalIngress=false` を指定してください。

```bash
az deployment group create \
  --resource-group <resource-group> \
  --template-file deploy/main.bicep \
  --parameters acrName=<acr-name> imageTag=<tag> externalIngress=false
```

## MCP クライアント設定

URL ベースの Streamable HTTP を使える MCP クライアントには、デプロイ後の URL を登録します。

```json
{
  "mcpServers": {
    "yahoo-finance": {
      "url": "https://<container-app-fqdn>/mcp"
    }
  }
}
```

クライアントによって設定キー名が異なるため、そのクライアントの Streamable HTTP 設定に合わせてください。

## 環境変数

| 変数 | 既定値 | 説明 |
|---|---:|---|
| `HOST` | `0.0.0.0` | リッスンアドレス |
| `PORT` | `8000` | リッスンポート |
| `LOG_LEVEL` | `INFO` | ログレベル |
| `MCP_STATELESS_HTTP` | `true` | リクエストごとのステートレス transport |
| `MCP_JSON_RESPONSE` | `false` | POST 応答を単一 JSON にするか |
| `MAX_HISTORY_ROWS` | `5000` | `get_history` で許可する最大返却行数 |
| `YF_TIMEOUT_SECONDS` | `20` | Yahoo Finance 履歴取得のタイムアウト秒 |

## 運用・セキュリティ上の注意

- サンプルの外部 ingress は認証を実装していません。本番環境では Azure Container Apps の組み込み認証、API Management、Front Door、IP 制限、または private ingress を追加してください。
- `minReplicas=1` はコールドスタートを避けます。コスト優先なら 0 に変更できます。
- Yahoo Finance の応答仕様、レート制限、データ遅延、利用可能性は変更される可能性があります。再試行・キャッシュ・監視は用途に応じて追加してください。
- yfinance は Yahoo, Inc. の公式 SDK ではありません。データ利用前に Yahoo の利用規約を確認してください。市場データは投資助言ではありません。
- 履歴 API の `end` は排他的です。分足などの intraday データには Yahoo 側の取得可能期間制限があります。

## ディレクトリ構成

```text
.
├── Dockerfile
├── pyproject.toml
├── src/yahoo_finance_mcp/server.py
├── tests/test_server.py
└── deploy/
    ├── deploy.sh
    └── main.bicep
```

## ライセンス

MIT
