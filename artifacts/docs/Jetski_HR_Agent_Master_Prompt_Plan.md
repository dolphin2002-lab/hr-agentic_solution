# HR Agentic Solution (MVP 1) — Jetski 実装・評価マスタープロンプト計画 (v1.4)

> [!IMPORTANT]
> **v1.4 主なアップデート内容**:
> 1. **`evaluation-plugins` 構造および全5スキルの完全統合**: GitHub リポジトリ (`https://github.com/pauldatta/evaluation-plugins`) のディレクトリ構造（`plugin.json`, `rules/sdd-evaluation-guardrails.md`, `skills/` 配下の 5 つの評価スキル）をリポジトリ直下に完全再現し、かつ Workspace-First Harness 原則に基づき `_agents/rules/` および `_agents/skills/` にも自動同期・展開する構成としました。
> 2. **Policy Handbook (`ALTOSTRAT SINGAPORE EMPLOYEE POLICY HANDBOOK & CONDUCT GUIDELINES`) の Vertex AI Search (VAIS) 自動インジェスト組み込み**: Google ドキュメント (`1ZyCYGkrmzJre94Etl8lJHJSBRaql2FZYidB432EMP-s`) をエクスポートし、GCS バケットへのアップロードおよび VAIS Data Store へのインポートを実行するスクリプト (`scripts/ingest_policy_to_vais.py`) と、ローカル開発用フォールバック (`data/policies/altostrat_singapore_policy_handbook.md`) を実装計画に統合しました。
> 3. **2階層シンプル・エージェント構成 (`backend.hr_agents`)**: `root_agent` + 3 Specialist Agents (`rag_agent`, `workweek_agent`, `service_immediately_agent`) 全モデルで **`gemini-3.8-flash`** を統一採用しています。

---

## 1. ワークスペース & 評価プラグイン (`evaluation-plugins`) 構成図

```text
hr-agentic-solution/
├── .env                                            # 環境変数 & MCP トークン管理 (Git追跡除外)
├── pyproject.toml                                  # uv プロジェクト設定ファイル
├── data/
│   └── policies/
│       └── altostrat_singapore_policy_handbook.md  # Google Doc (1ZyCYGkrmzJre94Etl8lJHJSBRaql2FZYidB432EMP-s) エクスポート原本
├── scripts/
│   ├── setup_harness.sh                            # evaluation-plugins のクローンと _agents/ へのシンボリックリンク/同期スクリプト
│   └── ingest_policy_to_vais.py                    # Policy Handbook を GCS 経由で Vertex AI Search Data Store へ投入するスクリプト
├── evaluation-plugins/                             # https://github.com/pauldatta/evaluation-plugins 完全準拠ディレクトリ
│   ├── plugin.json                                 # Plugin manifest and metadata
│   ├── rules/                                      # System rules and skill guardrails
│   │   └── sdd-evaluation-guardrails.md            # Behavioral guardrails for SDD evaluation
│   ├── skills/                                     # Modular skills directory (extensible)
│   │   ├── agent-eval-guide/                       # AI Agent Evaluation Design & Report Authoring Skill
│   │   ├── architecture-drift-evaluation/          # Code vs SDD Architecture Drift Evaluation Skill
│   │   ├── eval-adk-skill/                         # 4-Tier Golden Evalset Engineering & Evaluation for Google ADK
│   │   ├── eval-agentcli-skill/                    # ADK Agent Evaluation Runner & Preflight Check Skill
│   │   └── sdd-evaluation/                         # Software Design Document Evaluation Skill
│   └── README.md
├── _agents/                                        # Workspace-First Harness (Jetski エージェント・ハーネス認識用)
│   ├── rules/
│   │   └── sdd-evaluation-guardrails.md            # -> ../../evaluation-plugins/rules/sdd-evaluation-guardrails.md
│   └── skills/
│       ├── agent-eval-guide/                       # -> ../../evaluation-plugins/skills/agent-eval-guide/
│       ├── architecture-drift-evaluation/          # -> ../../evaluation-plugins/skills/architecture-drift-evaluation/
│       ├── eval-adk-skill/                         # -> ../../evaluation-plugins/skills/eval-adk-skill/
│       ├── eval-agentcli-skill/                    # -> ../../evaluation-plugins/skills/eval-agentcli-skill/
│       └── sdd-evaluation/                         # -> ../../evaluation-plugins/skills/sdd-evaluation/
├── backend/
│   ├── gateway.py                                  # Cloud Run Gateway (Redis Token Bucket, Cloud Tasks Queuing, SSF/RISC Revocation)
│   └── hr_agents/                                  # ADK Agent Module (`adk eval` エントリーポイント)
│       ├── __init__.py                             # root_agent エクスポート
│       ├── agent.py                                # Coordinator Agent (`root_agent`, model="gemini-3.8-flash")
│       ├── sub_agents.py                           # Specialist Subagents (`rag_agent`, `workweek_agent`, `service_immediately_agent`)
│       └── tools.py                                # VAIS Search Tool (ローカルフォールバック付) + MCPToolset (/work-week/mcp/, /service-immediately/mcp/)
└── evals/                                          # evaluation-plugins/skills/eval-adk-skill 準拠 4-Tier Golden Evalsets
    ├── test_config.json                            # Evaluation Criterion (tool_trajectory_avg_score: 1.0, response_match_score: 0.75)
    ├── rag_eval_golden.evalset.json                # Policy Handbook 準拠 4-Tier Golden Set (Sick leave 14d, Vacation 8yr=21d, Host gift $50, Room salon禁止等)
    ├── workweek_golden.evalset.json                # WorkWeek MCP ツール軌道検証 (12h shift=1.5d, Ramp-Back 2wks, Email delegation HRSD ticket)
    └── service_immediately_golden.evalset.json     # ServiceImmediately MCP ツール軌道検証 ($500 Remote Equipment Facilities ticket, Sequential state transition)
```

---

## 2. Jetski への指示用マスタープロンプト (コピー＆ペースト用)

以下のコードブロックを Jetski のチャット欄（`/plan` モード推奨）にそのまま貼り付けることで、エージェントの構築・VAIS インジェスト・`evaluation-plugins` による 4-Tier 評価およびアーキテクチャ・ドリフト検証を一気通貫で自動実行できます。

```markdown
/plan 以下の仕様および SDD (#8 of Enterprise Agentic Solution Design Document - MVP 1 - JP) に厳密に従って、HR Agentic Solution (MVP 1) の実装、Policy Handbook の Vertex AI Search (VAIS) インジェスト、および `evaluation-plugins` を用いた 4-Tier 評価・ドリフト検証を実施してください。

## 1. 絶対遵守ルール (Core Constraints)
1. **パッケージ・仮想環境管理**:
   - Python の環境構築・パッケージ追加は必ず `uv` (`uv init`, `uv venv`, `uv add`, `uv run`) を使用すること。グローバルな `pip install` は厳禁。
2. **使用モデルの統一**:
   - すべてのエージェント（Coordinator および Specialist Subagents）の LLM モデルには **`gemini-3.8-flash`** を指定すること。
3. **シークレット・トークン保護**:
   - MCP サーバーの Bearer トークンは絶対にソースコードにハードコードせず、`.env` から環境変数経由で読み込むこと：
     - WorkWeek Server (`/work-week/mcp/`): 環境変数 `WORKWEEK_MCP_TOKEN` = `<WORKWEEK_MCP_TOKEN>`
     - ServiceImmediately Server (`/service-immediately/mcp/`): 環境変数 `INCIDENT_MCP_TOKEN` = `<INCIDENT_MCP_TOKEN>`
4. **`evaluation-plugins` ディレクトリ構造とハーネス統合**:
   - `https://github.com/pauldatta/evaluation-plugins` をクローン（または `/tmp/evaluation-plugins` からコピー）し、プロジェクト直下に `evaluation-plugins/` (`plugin.json`, `rules/sdd-evaluation-guardrails.md`, `skills/` 配下 5 スキル) を完全な形で配置すること。
   - Workspace-First Harness 原則に従い、`_agents/rules/sdd-evaluation-guardrails.md` および `_agents/skills/{agent-eval-guide,architecture-drift-evaluation,eval-adk-skill,eval-agentcli-skill,sdd-evaluation}` へシンボリックリンク（またはコピー）を作成すること。

---

## 2. Policy Handbook のエクスポートと Vertex AI Search (VAIS) 自動インジェスト
対象ドキュメント: **`ALTOSTRAT SINGAPORE EMPLOYEE POLICY HANDBOOK & CONDUCT GUIDELINES`**
- Google Document ID: `1ZyCYGkrmzJre94Etl8lJHJSBRaql2FZYidB432EMP-s`
- URL: `https://docs.google.com/document/d/1ZyCYGkrmzJre94Etl8lJHJSBRaql2FZYidB432EMP-s/edit?tab=t.0`

### 実装タスク (`scripts/ingest_policy_to_vais.py`)
1. `/google/bin/releases/gemini-agents-gdocs/gdocs readonly read 1ZyCYGkrmzJre94Etl8lJHJSBRaql2FZYidB432EMP-s --comments=false` (または Google Drive Export API) を実行し、ドキュメント全文（Section 1〜7：Paid Time Off, Family-Building Leaves, Compassionate Leaves, T&E Guidelines, Ethics & Conduct, Confidentiality, Harassment & Conduct）を `data/policies/altostrat_singapore_policy_handbook.md` として保存する。
2. 同ファイルを Google Cloud Storage バケット (`gs://${GOOGLE_CLOUD_PROJECT}-hr-policies/altostrat_singapore_policy_handbook.md`) へアップロードし、Cloud DLP による PII マスキング設定を通した上で、Vertex AI Search (Discovery Engine API `DocumentServiceClient.import_documents`) の Data Store (`VAIS_DATASTORE_ID`) へ Layout Parser (500トークンチャンク / 100トークンオーバーラップ) 指定でインポートするスクリプトを作成する。
3. `backend/hr_agents/tools.py` の `search_hr_policy(query: str)` ツールは、まず Vertex AI Search Data Store に対してハイブリッド検索（Dense + BM25 + Reranking）を実行して引用元（Section番号・条項名）付きで結果を返し、ローカル開発環境等で VAIS が未接続の場合は `data/policies/altostrat_singapore_policy_handbook.md` に対するローカル・チャンク検索へシームレスにフォールバックする実装とすること。

---

## 3. シンプル 2階層 ADK エージェント構成 (`backend/hr_agents/`)

ADK CLI (`adk eval`) のモジュール要件に準拠した 2階層アーキテクチャを構築する：

* **`backend/hr_agents/agent.py` (`root_agent`)**:
  - Model: `gemini-3.8-flash`
  - Role: Coordinator Agent。ユーザーの意図を解析し、`rag_agent`, `workweek_agent`, `service_immediately_agent` にルーティングする。複合タスク（例：「8年勤務の有給日数を規定で確認し、12時間シフト1日分の休暇を申請し、1週間以上の医療休暇のためマネージャーへのメール委任チケット(HRSD, Priority 3)を起票する」）では、複数サブエージェントを順次呼び出して統合回答を生成する。
* **`backend/hr_agents/sub_agents.py`**:
  1. **`rag_agent`** (`model="gemini-3.8-flash"`):
     - Tool: `search_hr_policy`
     - Instruction: `ALTOSTRAT SINGAPORE EMPLOYEE POLICY HANDBOOK & CONDUCT GUIDELINES` に厳密に基づき回答する。必ず **「Prohibition Override Rule（絶対禁止条項の優先：例・1泊$50以下のホストギフトであってもギフトカード/現金は厳禁、1人$100未満であってもルームサロン/賭博等の成人向け娯楽は厳禁）」** および **「Calculation Rule（勤続年数別有給日数、12時間シフト=1.5日換算、Ramp-Back 2週間=勤務50%・給与100%）」** を適用し、該当セクション番号（例: Section 1.1, Section 4.3, Section 5.2）を明記して回答する。
  2. **`workweek_agent`** (`model="gemini-3.8-flash"`):
     - Tool: `MCPToolset` (`/work-week/mcp/`, Header: `Authorization: Bearer ${WORKWEEK_MCP_TOKEN}`)
     - Instruction: 休暇残高照会、有給・病気休暇・Ramp-Back 申請を実行。開始日が終了日より後の申請や残高超過申請は事前拒否し、2日超の病気休暇には48時間以内の診断書(MC)提出要件を案内する。破壊的変更・申請確定前には HITL (Human-in-the-Loop) 確認を行う。
  3. **`service_immediately_agent`** (`model="gemini-3.8-flash"`):
     - Tool: `MCPToolset` (`/service-immediately/mcp/`, Header: `Authorization: Bearer ${INCIDENT_MCP_TOKEN}`)
     - Instruction: ITSM チケットの作成・照会・ステータス更新を実行。
       - 1週間超の計画医療休暇時のメール委任申請は `Category: 'HRSD'`, `Priority: '3 - Moderate'` で起票する。
       - リモート/ハイブリッド勤務者のホームオフィス機器（上限 $500 USD）や海外拠点異動時の事前入館証設定は `Category: 'Facilities'`, `Priority: '3 - Moderate'` で起票する。
       - チケットのステータス変更は必ず **New → In Progress → Resolved → Closed** の順序を厳守し、中間ステータスのスキップ（New → Closed 直行など）を拒否する。軽微な問題（椅子のきしみ等）は `Priority: '4 - Low'` に設定する。

---

## 4. エンタープライズ・ガバナンス & Cloud Run ゲートウェイ (`backend/gateway.py`)
SDD の懸念事項対策および 4層データベース設計を実装する：
1. **レート制限 & 非同期キューイング (Section 5.3)**:
   - Memorystore for Redis による Token Bucket レート制限（WorkWeek: 500 req/min, ServiceImmediately: 300 req/min）と、上限超過時の Cloud Tasks (`hr-mcp-burst-queue`) への自動エンキュー。
2. **即時 OAuth トークン失効 SSF/RISC (Section 4.4)**:
   - `/webhooks/ssf-risc` エンドポイントにて従業員の異動・退職イベントを受信し、Redis ブラックリスト (`revoked_token:{jti}`) への即時登録と WorkWeek / ServiceImmediately OAuth トークンの強制無効化を行う。
3. **監査ログ・アーカイブ & GDPR Crypto-shredding (Section 4.5)**:
   - BigQuery 1年間 Hot 保持 → GCS Archive 7年間保持のライフサイクル設定と、退職者データの Cloud KMS 鍵破棄 (Crypto-shredding) + VAIS インデックス `PurgeDocuments` 処理。

---

## 5. `evaluation-plugins` 5スキルを用いた評価・検証フェーズ
1. **Golden Evalset の構築 (`eval-adk-skill` 準拠)**:
   - `evals/rag_eval_golden.evalset.json`: Altostrat Singapore Policy Handbook に基づく 4-Tier テストケース（Tier 1: 14日病気休暇+46日入院休暇、Tier 2: 勤続8年=21日有給+12時間シフト=1.5日計算、Tier 3: $45ギフトカード禁止・$80ルームサロン禁止の Prohibition Override、Tier 4: ペット忌引対象外・架空ポリシー拒否）。
   - `evals/workweek_golden.evalset.json` & `evals/service_immediately_golden.evalset.json`: MCP ツール呼び出し順序・引数検証を含む Golden Set。
2. **評価の自動実行 (`eval-agentcli-skill` & `agent-eval-guide` 準拠)**:
   - `uv run adk eval backend.hr_agents evals/rag_eval_golden.evalset.json --config_file_path evals/test_config.json --print_detailed_results` を実行し、結果を `evaluation_report.md` に出力する。
3. **SDD 評価 & アーキテクチャ・ドリフト監査 (`sdd-evaluation` & `architecture-drift-evaluation` 準拠)**:
   - `evaluation-plugins/rules/sdd-evaluation-guardrails.md` のガードレールに従い、実装コードと SDD (#8) の間にドリフト（乖離）がないかを検証し、`architecture_drift_report.md` にまとめる。
```
