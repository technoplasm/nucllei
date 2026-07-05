# Tools 機能キャッチアップ調査(issue #18)

- 調査日: 2026-07-05
- 対象: upstream v0.6.6 〜 v0.10.2 の**リリースノート本文のみ**(40 リリース)。
  ソースコード・タグ間 diff は未閲覧(クリーンルーム原則、`upstream-policy.md`)。
- 目的: #18 チェックリスト 1 項目目「Tools 関連機能のリスト化(外形仕様レベル)」と、
  2 項目目「llens に必要なものの選別」の判断材料。

## タイムライン(主要マイルストーン)

1. **v0.6.7–v0.6.11**: OpenAPI tool server の設定性向上(`TOOL_SERVER_CONNECTIONS` env、カスタム名/OpenAPI URL)
2. **〜v0.7.0**: MCP tool server 対応が既に存在(新機能として告知なし。v0.7.0 の fix 欄に初出 = v0.6.19〜v0.7.0 の間に導入)
3. **v0.7.0**: **Native Function Calling**(マルチステップ agentic ループ + interleaved thinking)。
   同時に「Tools/Tool Server のアクセス制御をバックエンドで強制(直接 API 呼び出しによるバイパスを閉鎖)」という **セキュリティ修正**
4. **v0.8.0**: **Skills(experimental)** 導入(`$` コマンド / モデルへの attach)。Open Responses protocol(experimental)
5. **v0.8.9–v0.8.11**: tool call **ストリーミングの堅牢化**(delta chunk 間での関数名重複、arguments に複数 JSON が混ざるケースの分割等)
6. **v0.9.0**: **Tools/Functions/Pipelines の async 化(breaking、プラグイン移行ガイドあり)**。reasoning を tool call ターン跨ぎで保持
7. **v0.9.5–v0.9.6**: tool call 反復上限が `CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS`(既定 30→256、-1 で無制限)に。上限到達時はチャット内エラー表示
8. **v0.10.0**: **Native tool calling が既定化**(旧挙動は "Legacy" と改名)。Skills はプロンプト全文注入からオンデマンド manifest 方式へ。構造化出力(reasoning / tool call)をクライアント側でレンダリング
9. **v0.10.2**: 小粒(tool 入力ダイアログの dismiss でリクエストをキャンセル)

## カテゴリ別カタログ(外形仕様レベル)

### tool-calling-core(呼び出しループ・プロトコル)

| 版 | 内容 |
|---|---|
| v0.7.0 | Native Function Calling: 1 会話でマルチステップ実行。Chat Controls で mode を "Native" に設定 |
| v0.7.0 | Interleaved thinking: 各 tool 出力を分析してから次ステップを決定 |
| v0.8.0 | Open Responses protocol(experimental): 接続を Chat Completions の代わりに Responses に。reasoning ストリーミング・richer tool call handling |
| v0.8.2 | tool call 履歴を assistant メッセージへ合成せず保持(multi-turn での幻覚 tool 出力を防止)。`finish_reason: "tool_calls"` を正しく返す |
| v0.8.9 | ストリーミング堅牢化: delta chunk 間の関数名重複、arguments 内の複数 JSON オブジェクト分割、引数なし tool call |
| v0.9.0 | reasoning content を tool call ターン跨ぎで保持 |
| v0.9.3 | tool call 往復メッセージで reasoning を `<think>` に包まない(content への漏出防止) |
| v0.9.6 | 反復上限 `CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS`(既定 256、-1 無制限)。上限到達時はチャット内エラー |
| v0.9.6 | 不完全な tool call / 孤児 tool result があるチャットを strict なプロバイダでも継続可能に |
| v0.10.0 | **Native が既定**(旧 "Default" は "Legacy" に改名、チャット/モデル/グローバルで opt-out) |
| v0.10.0 | 構造化出力(reasoning / tool call / server-side tool steps)をサーバーで平文化せずクライアントでレンダリング |

### tool-infra(ロード・実行基盤)

| 版 | 内容 |
|---|---|
| v0.6.6 | tool/function の frontmatter requirements を起動時 pip 自動インストール(v0.8.4 で opt-out、offline 時 skip) |
| v0.6.12 | function のロードを内容変化時のみ再実行(キャッシュ) |
| v0.8.0 | valve フィールドの password マスク / 動的 dropdown |
| v0.8.4 | tools 一覧の性能改善(content/specs の遅延ロード、モジュールキャッシュ) |
| v0.8.6 | tool 未添付リクエストで不要な DB クエリを skip。ストリーミング中の tool 結果処理の効率化 |
| v0.8.9 | tool が chat の files にメタデータ経由でアクセス可能 |
| **v0.9.0** | **Tools/Functions/Pipelines の async シグネチャ移行(breaking)** |
| v0.9.6 | completion が参照する tools を 1 バッチクエリで取得(N+1 解消) |
| v0.10.0 | valve 値の保存時暗号化(opt-in)。到達不能な tool server がモデルロードをブロックしない |

### tool-server / MCP

- OpenAPI tool server: env 設定(v0.6.7)、カスタム名/URL(v0.6.11)、user 情報ヘッダ転送(v0.8.0)、
  外部 tool からの event emitter(v0.8.0)、専用タイムアウト `AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER`(v0.8.11)、
  バイナリ応答(画像等)の処理(v0.8.11)、OpenAPI スキーマ互換性の堅牢化(v0.9.3)、
  リダイレクト SSRF の既定ブロック(v0.9.5)
- MCP: v0.7.0 までに導入済み。以後は主に OAuth 2.1 まわりの拡充(static credentials、scope 制限、
  discovery 互換性)と安定性修正(task cancellation、cleanup 時の CPU スパイク)。
  `MCP_INITIALIZE_TIMEOUT`(v0.9.6)

### skills(#5 の設計入力)

| 版 | 内容 |
|---|---|
| **v0.8.0** | **Skills 導入(experimental)**: 再利用可能な指示の束。チャットで `$` コマンド参照、またはモデルに attach |
| v0.8.2 | ユーザー選択 skill は全文注入、モデル attach skill は name/description のみ一覧提示 |
| v0.8.4 | JSON / Markdown からの import |
| v0.9.0 | skill ID での lookup(指示文に ID を含める) |
| v0.9.6 | 会話単位の skills on/off(Integrations メニュー) |
| v0.10.0 | モデル attach skill をオンデマンド manifest 方式に(system prompt へ全文注入しない)。インライン `$` 言及は全文注入のまま |

### permissions(#4 交差権限と整合させる領域)

| 版 | 内容 |
|---|---|
| **v0.7.0** | **Tools/Tool Server のアクセス制御をバックエンドで強制(直接 API バイパス閉鎖)** — v0.6.5 に同穴がある可能性。#24 で要確認 |
| v0.8.0 | tool の個人ユーザー共有(group 共有に加えて) |
| v0.8.2 | skills の共有権限(group 別 / public) |
| v0.9.5 | tool の Python ソース更新に `workspace.tools` 権限を要求(write 共有はメタデータのみ編集可) |
| v0.9.6 | ユーザー/グループの実効アクセスのプレビュー(models / knowledge / tools)。inline tool server の per-user 権限を completion リクエストで強制 |
| v0.10.0 | skills の import / export 権限を分離 |

### UI(参考。レンダリング刷新 #17/#20 と重なる)

- pending tool call の表示(v0.7.0)、引数の key-value 表示(v0.8.3)、
  連続 tool call / reasoning の collapsible グループ化(v0.8.11)、
  tool call 引数のイテレーティブなパース描画・折りたたみ時の遅延レンダリング・ストリーミング更新のバッチ化(v0.10.0)

### 対象外(組み込みツール群 — トリアージ済みで不要)

web search / knowledge / notes / chat 検索 / memory / automations / task 管理 / KB filesystem(`ENABLE_KB_EXEC`)/
Open Terminal 統合 / Anthropic Messages proxy / Channels の tool 対応。
これらは llens では DROP または放置領域の built-in tool であり、キャッチアップしない。

## llens 選別(提案)

### A. #18 で実装する(CORE)

1. **tool call ストリーミングの堅牢化**(v0.8.9 相当の外形): delta chunk 間の関数名重複・複数 JSON の分割・
   引数なし tool call。SGLang × Kimi K2.6 の実フレーム(#29 fixture)で再現ケースを固定してから実装。
2. **tool call 履歴の保持と `finish_reason: "tool_calls"`**(v0.8.2 相当): multi-turn の整合性。
   Kimi の reasoning 保持(v0.9.0/v0.9.3 相当の「reasoning をターン跨ぎで保持し `<think>` に包まない」)は
   #17(content blocks)と同一題材なので、データモデルは #17 と共同設計。
3. **反復上限の設定化とチャット内エラー表示**(v0.9.6 相当): 小粒で効果的。
4. **tools の N+1 解消・ロードキャッシュ**(v0.8.4/v0.9.6 相当): #28 の async 化とセットで。

### B. 隣接 issue に流す

- **tool ロード・実行の async 化**(v0.9.0 の breaking 移行に相当)→ **#28**(棚卸しから。外部 tool server 呼び出しの aiohttp 化を含む)
- **Skills の外形仕様**(`$` コマンド / モデル attach / オンデマンド manifest)→ **#5** の設計入力。
  llens は「Model プリセット流用が第一候補」だが、upstream の外形(全文注入 vs manifest)は参考になる
- **tool/tool server アクセス制御のバックエンド強制**(v0.7.0 セキュリティ修正)→ **#24** で v0.6.5 の該当有無を確認し、
  必要なら自前バックポート。**セキュリティ性の指摘なので優先度高**
- **tool call の構造化レンダリング** → #20(L3)
- **並列 tool call**: リリースノート上「新機能」としての告知は無く、v0.10.0 の Anthropic 互換 API 修正で言及されるのみ。
  複数 tool call 自体は v0.7.0 native mode の時点で存在すると推定。llens では **Kimi K2.6 が実際に並列 tool call を
  出すかを fixture 追加キャプチャで確認**してから要否判断(#15 の残タスクと合流)

### C. やらない

- MCP OAuth 2.1 まわり全般(air-gapped・院内 tool server は静的接続で足りる)
- Open Responses protocol(#15 で要否判断中。現状 SGLang は Chat Completions で足りる)
- pip 自動インストール(air-gapped で無意味。むしろ既定無効を維持)
- valve 暗号化・動的 dropdown 等の valve 拡張(必要になってから)
- built-in tools 全部(上記「対象外」)

### D. 要判断(ユーザー判断待ち)

- **MCP 対応そのもの**: OAuth 抜きの素の MCP(stdio/HTTP)を院内ツール接続の選択肢に加えるか。
  現状は OpenAPI tool server(v0.6.5 内蔵)+ nsjail sandbox で足りている認識
- tool server への user 情報ヘッダ転送(v0.8.0 相当): 院内監査(#31)で per-user 監査が要るなら採用価値あり

## 次の一手(#18 チェックリスト対応)

1. ~~リスト化~~ → 本メモ
2. 選別 → 上記 A〜D 案をレビューして確定(D は判断が要る)
3. SGLang × Kimi の tool call フレームを #29 の基盤で追加キャプチャ(並列 tool call・不正 JSON・引数なしの実挙動)
4. A-1/A-2 をテストファーストで実装(fixture リプレイ → middleware/utils/tools.py)
5. 自前ツール(nsjail+IPython、de-id)が乗ることの確認テスト
