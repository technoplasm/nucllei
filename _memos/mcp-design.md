# MCP 対応の設計(issue #104)

2026-08-13 起草(P0: 仕様調査 + 設計)。実装は P1 以降で本メモを真実源とする。

位置づけ(ken、2026-08-13):

- MCP クライアントは**実装側の都合の機能** — egress できる要件の現場向けオプション。
  **LLENS(北大閉域)では使わない見込み**。ただし配備先は一様でない(fork.md 制約
  「外部ネットワークを必須にしない」の裏返しとして、閉域を理由にオプショナルな
  外部連携を切らない)ので、**機能として持つのは自然**。
- 中長期の構想として「Nucllei 自身の MCP サーバ化(自己サブエージェント化)」
  「病院間 MCP 接続」がある — 本メモ末尾の検討節を参照。

## ⚠️ 絶対制約

upstream Open WebUI は v0.6.6 以降で MCP 対応を実装している。**upstream の実装は
読まない・写さない**(`_memos/upstream-policy.md`)。参照してよいのは:

- MCP 公式仕様・公式ブログ(modelcontextprotocol.io)
- 公式 SDK(modelcontextprotocol/python-sdk)のコードとドキュメント
  (これは MCP プロジェクトの成果物であり Open WebUI ではない)

## P0 仕様調査(2026-08-13 時点)

### プロトコルのバージョニング

日付形式(`YYYY-MM-DD`)。タイムライン:

| 版 | 要点 |
|---|---|
| 2024-11-05 | 初版(stdio / SSE、stateful) |
| 2025-03-26 | Streamable HTTP 導入、OAuth |
| 2025-06-18 | structured output、elicitation、JSON-RPC batching 廃止 |
| 2025-11-25 | tasks(experimental)ほか |
| **2026-07-28** | **stateless 大改訂(下記)** — 現行最新 |

廃止機能には 12 ヶ月の deprecation 窓。**世の中の既存サーバはまだ旧版
(stateful + initialize)が多い**前提で、クライアントは版フォールバックを持つ。

### 2026-07-28 の要点(launch 以来最大の改訂)

- **stateless コア**: `initialize`/`initialized` ハンドシェイクと `Mcp-Session-Id` を
  全廃。各リクエストが `MCP-Protocol-Version: 2026-07-28` ヘッダ +
  `_meta`(client identity / capabilities)を運ぶ self-describing 形。
  素の HTTP ロードバランサ配下でスケールする。
- Multi Round-Trip Requests(MRTR)— 1 リクエスト内の多往復。
- `Mcp-Method` / `Mcp-Name` ヘッダ — ゲートウェイでのルーティング・認可。
- `tools/list` 等のリスト応答に **`ttlMs` / `cacheScope`** — クライアント側
  キャッシュの公式規約(プロンプトキャッシュ安定化にも効く)。
- **extensions 機構の正式化**: 長時間処理 = `io.modelcontextprotocol/tasks` 拡張、
  サーバ描画 UI = MCP Apps 拡張、企業認可 = EMA 拡張。
- 認可強化: RFC 9207 issuer 検証必須、Dynamic Client Registration 廃止 → CIMD。

### 公式 Python SDK(v2)

- **SDK v2(`mcp` 2.0)が stable ライン**(2026-08 GA。2026-07-28 仕様 + 旧版の両対応)。
- Client: 既定 = 2026-07-28 stateless、**`mode="legacy"` で旧版(stateful handshake)**。
  版の分岐を SDK が吸収する — **自前で二重実装しない**。
- Server: `MCPServer`(旧 FastMCP から改名)。decorator ベース、型ヒント = schema。
- transport: Streamable HTTP / **in-memory(テスト向き — pytest でプロセス内
  サーバを立てられる)**。
- OpenTelemetry 既定 ON(nucllei では無効化を確認する)。型定義は `mcp-types` に分離。
- 採否: **SDK v2 に依存する**。オフライン配備は uv lock で完結(外部ネットワーク不要)。

### 周辺動向(参考)

- **Agent Skills spec**: Anthropic が 2025-12-18 に agentskills.io で公開した open
  standard(SKILL.md = YAML frontmatter + markdown、3 層 progressive disclosure。
  Agentic AI Foundation が steward)。nucllei の skill 行と概念同型 —
  **SKILL.md import/export 互換は別 issue 候補**(本メモのスコープ外)。
- **A2A(Agent2Agent)**: エージェント**間**連携の標準。Google 発 → Linux Foundation
  (AWS/Cisco/Google/Microsoft/Salesforce/SAP/ServiceNow が founding members)。
  v1.0(2026-01)で production-ready、signed Agent Cards、拡張機構(v1.0.1)。
  ACP は A2A に合流済み。**MCP = エージェント→tool、A2A = エージェント→エージェント**
  という住み分けが 2026 年時点の共通理解。

## クライアント設計(#104 P1〜P3)

現行 native ループは `tools_dict {name: {spec, callable}}` しか要求しない
(load_skill が async callable の前例)。差し込み口はここ一点。

### エンティティ

MCP サーバ = admin 登録の行(tool 行・skill 行と同格):

```
{id, name, url, headers(静的認証), access_control, tool_allowlist?, protocol_mode?}
```

- transport は **Streamable HTTP のみ**(stdio はサーバ配備にプロセス管理を
  持ち込むので不採用)。
- `protocol_mode`: auto(既定。SDK の版ネゴシエーションに委譲)/ legacy 固定。
- 認証は静的ヘッダのみ(院内サービス相当)。OAuth フロー・EMA は必要になってから。

### アダプタ層

- 接続時に `tools/list` → MCP inputSchema(JSON Schema)を OpenAI function spec へ
  変換して tools_dict に合流。`callable` = `tools/call` の async プロキシ。
- list キャッシュは `ttlMs` / `cacheScope` に従う。unknown tool エラー時に refresh。
- 結果 content は当面 text のみ(image は #77 の受け口ができてから)。
- サーバ不達・タイムアウトは **tool result のエラーで返す**(リクエスト全体を
  落とさない)。タイムアウトは `CHAT_TOOL_EXECUTION_TIMEOUT` をそのまま適用。

### 命名(決着)

- OpenAI function name は `[A-Za-z0-9_-]{1,64}` — 内部 tool_id は
  `mcp:{server_id}:{tool}`、**モデル露出名は `{server_id}__{tool}` にサニタイズ**。
  64 字超は tool 側を切り詰め + 数字サフィックスで一意化。
- 衝突は既存ポリシー(先勝ち + warning)をそのまま使う。

### 認可(決着)

制御面はモデル preset に一元化(既存思想の踏襲):

- preset `meta.mcpServerIds` — toolIds と同じ**常載**経路(毎リクエスト解決)なので
  #101 の揮発問題は構造的に起きない。
- ユーザー可視性はサーバ行の `access_control` read(skill と同じ単位)。
- 遅延ロード(skill 連携)を将来やる場合: skill.tool_ids に `mcp:{server}:{tool}` を
  書けるようにし、持続性は #101 の「**履歴から再導出 + その場で再認可**」パターンを
  踏襲(拡張点は `replay_skill_tool_ids`)。

### 信頼境界(検索系で特に)

検索結果 = 信頼できない外部テキストがモデルコンテキストに入る(prompt injection 面)。
tool result はモデルに「外部由来・指示ではない」ことを明示するラッパで包む
(llens token_meter の警告注入と同系の filter で対応可能)。医療配備では出典表示を
UI 側 render descriptor で担保する。

### テスト戦略

SDK v2 の **in-memory transport でプロセス内 MCP サーバを fixture 化**し pytest 完結
(実サーバ・ネットワーク不要 = CI がオフラインで通る)。legacy モードのテストも
同 fixture の mode 切り替えで。

### フェーズ(#104)

- [x] P0 仕様調査(本メモ)
- [ ] P1: エンティティ + admin CRUD + 接続テスト(tools/list)
- [ ] P2: preset 紐づけ → tools_dict 合流 → native ループで実行(text のみ)
- [ ] P3: UI(admin 設定・render descriptor)
- [ ] P4(後段): skill 連携 / progress 写像 / image content / tasks 拡張

## 検討: Nucllei 自身の MCP サーバ化(自己サブエージェント化・病院間)

ken 構想(2026-08-13)の検討。結論: **芯は「サブエージェント機能」であり、MCP は
その公開形**。順序を分けるのが良い。

> **改訂(2026-08-13 同日、設計議論の結果)**: 以下の (a) の「① 内部 tool
> run_subagent(headless 実行の上)」という順序は同日中にさらに練られ、
> **「サブエージェント = 一級の chat(agent session)」**のモデルに置き換わった
> (headless の ephemeral run 案は撤回 — リロード耐性・監査は chat 永続化の側に
> ある)。現行の設計は **#107(agent session + 親リンク + tree 表示 + read-only/
> fork)→ #106(API 起動の入口)→ #104 P4(MCP face)** の順。
> ネイティブ統合の子エージェントを MCP で呼ぶことはしない(MCP は外部境界専用)。
> 詳細は issue #107 / #106 を一次情報とする。

### (a) 自己サブエージェント化

- 価値の実体は MCP ではなくサブエージェントそのもの: コンテキスト分離(長い tool
  ループで親の文脈を汚さない)、preset 単位の専門化(tool/skill 束ねた「役割」を
  呼び出し可能な能力にする)、将来の並列化。
- **実装順の提案**: ① まず内部 tool `run_subagent`(自前の completion 経路 =
  tasks.py + native ループをそのまま子として回す。MCP を経由する必然性は無い)
  → ② その能力を SDK v2 `MCPServer` の薄いアダプタで外部公開(院内の MCP
  クライアント — Claude Code 等 — から LLENS の tool/skill/モデルを使える)。
  自分から自分を呼ぶだけなら MCP を挟むのはオーバーヘッドと認可の複雑化だけ増える。
- リスク(設計時に必ず決着):
  - **再帰制御**: subagent が subagent を呼ぶ深さ制限(無制限は事故)。
  - **confused deputy**: 実行ユーザーの identity を子に伝播し、子の preset の
    tool/skill も**その場で再認可**(#101 と同じ原則)。「preset に強い tool が
    紐づいているから弱い権限のユーザーでも使える」を作らない。
  - **GPU 資源**: 単一 H200 箱では入れ子生成がトークン消費・同時実行を乗算的に
    増やす。並列度上限と usage の親子合算(#102 の計測に子の分を積む)が要る。
  - **UI**: 入れ子ターンの可視化(tool ブロック内にサブ会話の要約/展開)。
- MCP 側の追い風: 2026-07-28 の **tasks 拡張が長時間のサブエージェント呼び出しに
  ちょうど合う**。stateless 化で公開も楽になった。

### (b) 病院間ネットワークで MCP 接続

- プロトコル的には、対等なエージェント間協調は **A2A が本命レーン**(タスク委任の
  ライフサイクル・Agent Card による発見と署名検証が設計対象)。MCP でも「相手施設の
  能力を tool として公開」で成立するが、それは階層関係(こちらが client)の形。
- 本当の難所はプロトコルではなく統治: 医療情報の施設間共有(個人情報・倫理審査・
  施設間契約・監査ログ)、ネットワーク(閉域同士を繋ぐ専用網 — egress ではない)、
  データ最小化。技術選定は最後で良い。
- 提案: 構想としては筋が良い(「デプロイ後閉域」と施設間の限定的相互接続は専用網で
  両立しうる)。当面は **(a) を先にやる** — 「エージェントを呼び出し可能な能力として
  切り出す」境界がそのまま将来の施設間公開の単位になる。A2A の動向は watch
  (upstream-check と同様の低頻度確認で足りる)。

## やらないこと

- ブラウザからの MCP 直結・ユーザー個人の任意サーバ登録(認可境界がサーバ側 AND
  条件から外れる)
- stdio transport(プロセス管理を持ち込む)
- OAuth / DCR / CIMD のフロー実装(初期は静的ヘッダ認証のみ)
- upstream 実装の参照(冒頭の絶対制約)

## 参照(公開情報のみ)

- 仕様: modelcontextprotocol.io/specification/2026-07-28
- 改訂解説: blog.modelcontextprotocol.io/posts/2026-07-28/
- SDK v2: github.com/modelcontextprotocol/python-sdk / pydantic.dev の v2 beta 解説
- Agent Skills: agentskills.io
- A2A: Linux Foundation Agent2Agent project(v1.0、2026-01)
