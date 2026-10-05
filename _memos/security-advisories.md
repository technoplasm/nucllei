# Open WebUI セキュリティアドバイザリ棚卸し — v0.6.5 影響判定(issue #24)

調査日: 2026-07-05 / 対象フォーク: llens (open-webui v0.6.5 固定, 北大病院オンプレ向け)

> **2026-07-17 追記(棚卸し)**: 本文は 2026-07-05 時点のスナップショット(製品は現在 Nucllei に改名、
> `backend/` → `./nucllei` にフラット化 — 本文中の `test/apps/...` 等のパスは現 `nucllei/test/apps/...`)。
> その後の機能撤去により、当時「コードが存在」だった以下は**機能ごと消滅し現行ツリーでは非該当**:
> channels / memories / chat tag / temporary chat / `/utils/code/execute` / `/utils/gravatar`(いずれも #67)、
> retrieval router・RAG/KB(#52・#41 で knowledge テーブルごと drop)— これにより Tier 3 の
> RAG/KB 認可群・channels 群、Tier 1 の GHSA-hmjq(memories)等は対象喪失、
> prompts(#35 → GHSA-4r4w の prompt history 面も喪失)、
> code_interpreter(#23)、リクエスト由来 tool_ids(#54 でサーバー側解決に変更)、
> api.openwebui.com 通信(update check / CUSTOM_NAME、#10/#34)。
> 引き続き存在する面: 認証(LDAP/OAuth コードは off で温存)・audio・files・tool_servers・tasks。

## クリーンルーム順守メモ

本レポートは GitHub Security Advisory API が返す **アドバイザリ本文・CVE 説明・影響バージョン範囲・修正バージョン・リリースノート** のみを根拠にしている。
アドバイザリ本文中に含まれる **修正コミット URL・blob 行リンク・パッチ差分は一切開いていない**(`_memos/upstream-policy.md` の制約)。
機能の存在有無は「フォーク自身の作業ツリー(= v0.6.5 ベース)」を grep して確認した(自コードなので参照可)。

## サマリ(数字)

- **公開アドバイジ総数: 111**(`gh api /repos/open-webui/open-webui/security-advisories --paginate`、全て state=published)
- **バージョン範囲外(v0.6.5 は非該当): 19 件**(0.6.5 以前で既に修正済み or 0.6.6 以降で導入された機能)
- **範囲内だがフォークに当該機能が無い: 約 11 件**(notes / terminal proxy / calendar / 協調ドキュメント ydoc / responses パススルー / Pyodide)
- **v0.6.5 に影響しうる: 約 81 件**(コードが存在。ただし多くは RAG/web検索/channels/OAuth/Ollama など既定オフ機能で条件付き)
- **v0.10.2 で「意図的に未列挙・一時的に伏せられた」修正の存在が明記** → 現時点で **非公開(embargo)相当のアドバイザリが不定数存在**(下記「embargo」節)

## 実施状況(2026-07-05 バックポート、master に出荷済み)

バックエンド分を Opus セッションで消化(いずれも脆弱性を実コードで確認 → 修正 → **修正前に落ちる**回帰テスト付き):

| GHSA / CVE | 深刻度 | 内容 | commit | issue |
|---|---|---|---|---|
| GHSA-2r4p-jpmg-48f4 / CVE-2026-44551 | CRITICAL | LDAP 空パスワード認証バイパス | 03e9a9783 | #38 |
| GHSA-482j-2pq6-q5w4 / CVE-2026-45672 | HIGH | code/execute の ENABLE_CODE_EXECUTION 未適用 | 03e9a9783 | #39 |
| GHSA-m8f9-9whg-f4xr / CVE-2026-45315 | HIGH | audio transcription 拡張子詐称 XSS + /cache 配信 | b582c862e | #39 |
| GHSA-p4fx-23fq-jfg6 / CVE-2026-45395 | HIGH | tool 更新のソース書換 authz(RCE 面) | 593f35c27 | #37 |
| GHSA-4pcg-253r-rf9w / CVE-2026-45350 | HIGH | リクエスト由来 tool_ids のアクセス検証 | be49c0bef | #37 |
| GHSA-7429-hxcv-268m / CVE-2026-34222 | HIGH | 管理者 valve 読み取り authz | 7e419395d | #37 |
| GHSA-gfm2-xm6c-37qc / CVE-2026-45349 | HIGH | chat completion の chat_id 所有者検証 | d60d13724 | #37 |
| GHSA-vjqm-6gcc-62cr / CVE-2026-54012 | HIGH | 偽装 chat-file / model meta.knowledge で他ユーザファイル読取(IDOR) | (本コミット) | #40 |
| GHSA-vrhc-3fr6-pc3c / CVE-2026-54010 | HIGH | 同上(read/削除面) | (本コミット) | #40 |
| GHSA-r472-mw7m-967f / CVE-2026-45402 | HIGH | KB Attach / process で file_id 未検証 | (本コミット) | #40 |
| GHSA-4g37-7p2c-38r9 / CVE-2026-45398 | HIGH | Retrieval API の KB/collection ACL バイパス(IDOR) | (本コミット) | #40 |
| GHSA-h3ww-q6xx-w7x3 / CVE-2026-45675 | HIGH | LDAP/OAuth 初回ユーザ競合→複数 admin | (本コミット) | #38 |
| GHSA-57q6-fvp4-pqmm / CVE-2026-45339 | HIGH | API キーの制限エンドポイント回避(空 allowlist で全許可) | (本コミット) | #38 |
| GHSA-7rw5-9f7q-xj36 | HIGH | ログインタイミングによるアカウント列挙 | (本コミット) | #38 |
| GHSA-v8qj-hxv7-mgvv | (MED) | mermaid securityLevel 'loose' で図ラベル/click から XSS | (本コミット) | #36 |
| GHSA-r29h-37fj-x2w6 | (MED) | SVG レンダラ(SVGPanZoom)未サニタイズ | (本コミット) | #36 |
| GHSA-cqp4-qqvg-3787 | (MED) | Banner のサニタイズ順誤り(sanitize→parse) | (本コミット) | #36 |
| GHSA-gf5m-wcrh-7928 | (MED) | Model Description の marked 出力未サニタイズ | (本コミット) | #36 |
| GHSA-8gh5-qqh8-hq3x / CVE-2025-46571 | HIGH | HTML アップロードが iframe で same-origin 実行 | (本コミット) | #36 |

**#38 残の修正範囲(2026-07-05)**: 認証系 3 件(CRITICAL の LDAP 空 PW は 03e9a9783 で出荷済み)。
- **h3ww(複数 admin)**: `Auths.insert_new_auth` に `promote_first_user_to_admin` を追加し、
  「user 数 0 → admin」判定と auth+user 挿入を**単一トランザクション + Postgres advisory lock**で直列化。
  role 決定の権威を呼び出し側の事前 count 読取から insert 内へ移動(signup/LDAP/OAuth の 3 経路を一本化)。
  OAuth は `get_user_role` の count ベース admin 分岐を除去(claims/既定ロール判定は保持)。
- **57q6(API キー制限回避)**: `utils/auth.py` の allowed_endpoints 解析で**空要素を除外**。
  既定 `""` や末尾カンマだと `[""]` → `startswith("/")` で全 path 一致し制限が全許可に無効化されていた。空リスト=全拒否に。
- **7rw5(タイミング列挙)**: `authenticate_user` がユーザー不在時も**固定ダミーハッシュで bcrypt 検証**を実行し応答時間を一定化。
- 回帰テスト: `test/apps/webui/routers/test_auths.py`(6 ケース。旧コードで落ちる: 空 allowlist=200、不在時 verify 未実行 等)。

**#36 の部分修正(2026-07-05、レンダリング刷新 #17 と衝突しない自己完結な面のみ)**:
- **コア markdown の html トークンは既に DOMPurify 済み**(`MarkdownTokens.svelte` / `MarkdownInlineTokens.svelte`、
  iframe は `sanitizeIframeHtml`=7c8eba59b)。9f4f 相当のチャット本文経路はサニタイズ済みと確認。
- **v8qj(mermaid)**: `CodeBlock.svelte` の `securityLevel` を `'loose'`→`'strict'`(ラベル HTML/click を無効化)。
- **r29h(SVG)**: `SVGPanZoom.svelte` の `{@html svg}` に `DOMPurify.sanitize` を適用(import 済みだが未使用だった)。
- **cqp4(Banner)**: `DOMPurify.sanitize(marked.parse(...))` へ順序修正(旧: 生 md を sanitize→parse は生成 HTML 素通し)。
- **gf5m(Model Description)**: `ChatPlaceholder.svelte` / `Placeholder.svelte` の marked 出力を DOMPurify で包む。
- **8gh5(HTML アップロード配信)**: `{{HTML_FILE_ID}}` は `/files/{id}/content/html` を iframe で読み込み、
  従来は same-origin で生 HTML が実行され得た(トークン窃取→乗っ取り)。iframe を `sandbox="allow-scripts"`
  (same-origin 剥奪)にし、`utils/index.ts` の生成側と `MARKDOWN_IFRAME_SANITIZE_CONFIG` の ADD_ATTR に `sandbox` を追加。
  バックエンド `routers/files.py` の `/content/html` に CSP `sandbox allow-scripts` + `nosniff` を付与(多層防御)。
  回帰テスト: `test/apps/webui/routers/test_file_html_serving.py`。
- 型チェック(svelte-check)は変更行にエラー無し。**mermaid/Artifacts の描画は視覚スモーク未実施**(#12 に合流可能。
  strict は mermaid 既定で図は描画されるが HTML ラベルは文字表示・click 無効になる)。

**#36 の残トリアージ(2026-07-05、実コード確認)**:
- **w7xj(Insert Prompt as Rich Text)= 構造的に低リスク**: `RichTextInput.svelte` は TipTap/ProseMirror。
  `editor.commands.setContent(marked.parse(...))` は schema でパースし未知タグ/属性を捨て script を実行しない({@html} 非経由)。
- **6gh2 / 3wgj(Profile Picture / OAuth picture SVG)= 非該当**: プロフィール画像は `<img src>` で描画され
  ({@html} での SVG インライン描画は無し)、`<img>` 内の SVG はスクリプトを実行しない。OAuth も data-uri を img 描画。
- **jwf8 / hcwp(Excel/DOCX プレビュー)= 非該当**: fork に mammoth/xlsx 等のプレビュー実装が無い。
- **3vv5(postMessage)= 低/非該当**: 受信ハンドラ(Feedbacks/Functions/Prompts/Models/ShareChat)は全て
  `event.origin !== url` を検証済み。かつ対向は openwebui.com コミュニティ連携(fork では機能ごと撲滅済み #10)。
- **xc8p / vjm7(iframe 埋め込み残)**: 上記 8gh5 の sandbox 化とコアの `sanitizeIframeHtml` でカバー。
- **9f4f(MarkdownTokens 本体)**: コア済み(DOMPurify)。#17 レンダリング刷新で再検証する。
- ⇒ **#36 のバックエンド/自己完結面はここで一段落**。残るは #17 刷新に伴う再検証と #12 視覚スモークのみ。

**#40 の修正範囲(2026-07-05)**: file_id / collection 参照の読取権限突合を一元化。
- `utils/access_control.py` に `has_read_access_to_file` / `has_read_access_to_collection` を新設
  (所有者 / admin / file 自身の access_control(**None=非公開**、has_access の None=public とは別扱い)/ 所属 KB の read)。
- チャット経路(`utils/middleware.py`): クライアント供給 `files` を `filter_files_by_user_access` で絞る。
  direct モードは `model.meta.knowledge` も検証(偽装経路)、非 direct は model アクセス検証済みで信頼。
- retrieval 経路(`routers/retrieval.py`): `/process/file`・`/query/doc`・`/query/collection` に read 権限ゲート。
- 回帰テスト: `test/apps/webui/utils/test_file_access_idor.py`(修正前の None=public バグを実際に検出)。
- **folder 経路は v0.6.5 に該当機能なし**(`routers/folders.py` は全て user スコープ、KB Attach 無し)→ 非該当。
- **GHSA-26g9-27vm-x3q8**(共有チャット branch の access_type 無視で他人ファイル削除、<=0.8.12): v0.6.5 の
  chat branch 経路に該当実装が無く、file 削除は `routers/files.py` が所有者検証済み → 現状非該当。#40 では扱わない。

**非該当と確認(捏造回避)**:
- **GHSA-j2c8-v969-8r5c**(/cache sibling-prefix traversal): Starlette 1.3.1 が `lookup_path` で
  `os.path.commonpath` による包含チェックを行っており脱出不可(実コードで確認)。
- **GHSA-9vvh-qmjx-p4q8**(base_model_id チェーン ACL バイパス): llens 非該当。
  ① 生モデル(workspace 行なし)は `check_model_access` が role=user に対し "Model not found" で既に遮断、
  ② preset 作成(`base_model_id` 指定)は `workspace.models` 権限=既定 admin のみ、
  ③ SGLang 単一接続で「制限されたベース計算資源」も無い → ユーザーは restricted なベースに到達不能。
  派生タスク: 生モデルをチャットピッカーから全員向けに隠す(UX、フロント/#6 で対応)。

**フロント/mixed**: #36(レンダリング/アップロード XSS 一括)は対応済み・close(7c8eba59b / 9e8ee5abc / 67a8de036)。
9f4f 相当のコア markdown 経路はサニタイズ済みを確認。再検証は #17、視覚スモークは #12 で。
**継続監視**: v0.10.x の未公開 access-control 修正(下記 embargo 節)。

## 2026-10-05 リリースノート照合で見つかった #24 未記載分(対応済み)

upstream のリリースノート(記述テキストのみ。リンク先・ソースは未閲覧)と v0.6.5 ベースの自コードを
照合して見つかった穴。いずれも **GHSA としては公開されておらず、本メモ(#24)の棚卸しに載っていなかった**。
再現は scratchpad の再現スクリプトで確認し、直し方は自前で設計した。各修正に回帰テスト付き。

| 内容 | 深刻度(自己評価) | commit | 回帰テスト |
|---|---|---|---|
| parentId が循環した履歴を保存させると `get_message_list` が無限ループし、イベントループごとサーバー全体がハング(DoS)。append-only ガード(#31)は content しか見ず通していた。`get_message_list` に visited ガード + 保存 / 作成 / インポートで「parentId 実在・循環なし・currentId 実在」を検証し 400 | HIGH(一般ユーザーで全体停止) | 573ebbb59 | `test_chats.py::TestChatHistoryValidation`、`test_message_helpers.py` |
| group の permissions が null(作成時に省略)だと権限合成が落ち、メンバーの API が全て 500。読み側で null を `{}` 扱い、作成・更新時に `{}` へ正規化 | MED(可用性) | d7df6cedd | `test_access_control.py::TestNullGroupPermissions` |
| trusted header 認証で 2 人目以降が自動登録されない(/signup 経由のため、1 人目で ENABLE_SIGNUP が落ちると 403)。ヘッダ認証専用の登録に分離 | MED(可用性) | 320d39194 | `test_auth_trusted_oauth.py` |
| trusted header のメールと cookie の JWT のユーザーを照合していない — 共有 PC で前の人のセッションのまま次の人が使える。不一致は 401 → 再サインインでヘッダ側のユーザーに入れ替わる | HIGH(なりすまし。共有端末運用に直撃) | 320d39194 | 同上 |
| OAuth ロール管理で許可 / admin ロールのどちらにも一致しないと DEFAULT_USER_ROLE に落ちてログインできた。fail closed(403)に | MED(OAuth は既定 off) | 320d39194 | 同上 |
| admin のユーザー追加(`/auths/add`)が返す「新ユーザーとしての token」に exp が無い(恒久資格情報)。SESSION_IDLE_TIMEOUT(無期限設定でも上限 1 時間)で期限付きに | LOW〜MED | 320d39194 | 同上 |
| tool server(`server:N`)の実行時に接続設定の read ACL を見ていない — 一覧では隠すが、preset の `meta.toolIds` 経由で ACL 外のユーザーも実行できた(下記詳細) | HIGH(共有キーで外部サービスを叩ける) | 8c1ec89ef | `test_get_tools_isolation.py::test_tool_server_execution_enforces_read_acl` |

**tool server(server:N)実行時の ACL 欠落の詳細**(filter / tool 担当の検出・修正。対応する GHSA は特定していない):
- 症状: 外部 OpenAPI tool server は `GET /api/v1/tools` の一覧では接続設定の
  `config.access_control`(read)で非 admin から隠していたが、実行経路 `utils/tools.get_tools` の
  `server:` 分岐は ACL を見ずに spec と callable を載せていた。preset の `meta.toolIds` に
  `server:N` が載っていれば、ACL 外のユーザーもその preset 経由で tool server を実行できた
  (`auth_type=bearer` なら admin が設定した共有キーで外部サービスを叩ける)。
- 修正(8c1ec89ef): `get_tools` の `server:` 分岐で一覧と同じ判定(admin バイパス /
  `has_access(read, config.access_control)`、None=全員)を実行時にも行い、外れたら載せない。
  skill 由来(`enforce_access_control=False`)は従来どおり。範囲外・未取得の `server:N` は
  IndexError / assert でターンを落とさずスキップ。
- 付随: tool / function の per-user valves endpoint(`/valves/user*`)は参照先 tool の read ACL を
  見ていなかったが、UserValves 機構ごと撤去(398e5b3d3 / 8c1ec89ef)したため面ごと消滅。

同じ照合で直した #24 外(機能バグ)の分は refs #142: payload(presence_penalty / top_k / min_p /
repeat_penalty が OpenAI 互換へ送られない・stop 文字列の 1 文字分割)eb7cffb2c、Postgres の NUL 入り
メッセージで検索 500 cd4c687c8、音声の外部呼び出しにタイムアウトが無い 333af7b52。

### 受容(対応しない)判断

- **GHSA-h2cw-7qw9-56xr / GHSA-jh9g-8jqw-m2qx(system prompt 露出)** — 一般ユーザーに preset の params
  (system prompt を含む。`/api/models` の `info.params`)が見える件は **対応しない(受容)**。
  ken 判断(2026-10-05):「system prompt は見えてもいい。モデル自体があまり隠してもくれないので」

## 最重要 TOP 3(id と一言理由)

> 2026-07-05 棚卸し時点のスナップショット。現在は 3 件とも対応済み(1 → #36 / 2 → #38 / 3 → #39)。

1. **GHSA-9f4f-jv96-8766 / CVE-2025-46719**(HIGH, 範囲 = **0.6.5 ちょうど**, patched 0.6.6)
   MarkdownTokens.svelte の未エスケープ Markdown トークン由来の Stored XSS → アクセストークン窃取 → **完全なアカウント乗っ取り、Functions 経由で RCE**。対象ファイルはフォークに存在確認済み。**フォークの正確な基点バージョンを名指しする唯一級の未修正欠陥**で最優先。
2. **GHSA-2r4p-jpmg-48f4 / CVE-2026-44551**(**CRITICAL**, `<=0.8.12`, patched 0.9.0)
   LDAP 空パスワードで Simple Bind 認証バイパス。認証は CORE 領域、`routers/auths.py` に LDAP 実装あり。LDAP を有効化する運用なら**無認証ログイン**に直結。
3. **GHSA-482j-2pq6-q5w4 / CVE-2026-45672**(HIGH, `<=0.8.11`, patched 0.8.12)
   `/api/v1/utils/code/execute` が `ENABLE_CODE_EXECUTION=false` でも実行してしまう(フィーチャーゲート未適用)。**フォークは nsjail+IPython の Jupyter 実行系を保持**しており、この「実行ゲートを API 側で強制する」ロジックはフォークのクリティカル面に直撃。

## embargo / 非公開について(調査ステップ 2)

- API が返すのは全て `state=published`。ドラフト/embargo のものは API 外形からは列挙できない(仕様どおり)。
- **v0.10.2 リリースノートに明示の伏字宣言あり**:「Not all security fixes in this version may be enumerated in the fixed section — some may be withheld for a short time to give administrators time to upgrade.」
  → **v0.10.2 に含まれるが GHSA 未公開の access-control 系修正が不定数存在する**と読める。件数・ID は現時点で特定不能。issue #24 では「v0.10.x で公開追随を継続監視」を TODO とすべき。
- 参考: 2026-06-29〜07-02 に `< 0.10.0` / `>= 0.9.x` 帯の一括公開が起きており(下記)、v0.10.2 由来の残りが順次公開される流れとみられる。

---

## v0.6.5 に影響する主要アドバイザリと自前バックポートの外形スケッチ

> スケッチは全て「アドバイザリ本文に書かれた症状・エンドポイント名・外形仕様」からの再設計であり、upstream 修正コードは見ていない。

### Tier 1: 既定オン面・高深刻度・フォーク中核に直撃(最優先バックポート候補)

- **GHSA-9f4f-jv96-8766 (CVE-2025-46719, HIGH, =0.6.5)** — MarkdownTokens.svelte の Stored XSS→ATO/RCE。
  対策: チャットレンダラの Markdown トークン(特に HTML/インライン HTML トークン)を DOMPurify 等でサニタイズしてから描画。`{@html}` に生トークンを渡す経路を洗い出し、許可タグ・属性のホワイトリスト化。共有チャット/コミュニティ共有経路が拡散面になるため、`Enable Community Sharing` の既定オフも合わせて確認。
- **GHSA-cm35-v4vp-5xvx (CVE-2025-64496, HIGH, <=0.6.34)** — Direct Connections の SSE `execute` イベントによるコード注入→ATO/RCE。
  対策: 外部モデルサーバから来る SSE イベント種別を厳格な allowlist で受理し、`execute` 等のクライアント側任意実行イベントを破棄。フォークは OpenAI 互換接続が中核なので **SSE ストリームパーサの信頼境界**を要点検。既定オフだが接続経路そのものは中核。
- **GHSA-9vvh-qmjx-p4q8 (CVE-2026-44555, HIGH, <=0.8.12)** — base_model_id のモデルチェーンでアクセス制御バイパス。
  対策: `models.py` の create/import と、chat_completion のベースモデル解決時に、**チェーン先モデルへのアクセス権を再チェック**。openai/ollama のペイロード書き換え箇所でも解決後モデル ID の可視性を検証。CORE(group 権限)なので回帰テスト必須。
- **GHSA-gfm2-xm6c-37qc (CVE-2026-45349, HIGH, <=0.8.12)** — `/api/chat/completions` が chat_id と所有者を突合せず、他人のチャットを継続可能。
  対策: chat_completion 入口で `chat_id`(および message_id)の所有者/共有権限を検証してから処理。API キー利用時も同様。Filter パイプライン共有時に横断が起きる旨の指摘あり=CORE。
- **GHSA-p4fx-23fq-jfg6 (CVE-2026-45395, HIGH, <0.9.5)** — `POST /api/v1/tools/id/{id}/update` に `workspace.tools` 権限チェック欠落→サーバ側 Python 差し替えで実行。
  対策: 更新エンドポイントに作成側と同じ `workspace.tools` 権限チェックを追加。Tools はコード実行の信頼境界なので最優先級。
- **GHSA-7429-hxcv-268m (CVE-2026-34222, HIGH, <0.8.11)** — Tool Valves のアクセス制御不備。
  対策: valve(ツール設定値/秘密)の読み書きにユーザ/権限スコープを適用。技術ユーザの API キーが他ユーザに露出しない構造へ。
- **GHSA-4pcg-253r-rf9w (CVE-2026-45350, HIGH, <=0.8.5)** — chat completion の `tool_ids`/`tool_servers` がユーザ入力で、ツール制限をバイパス。
  対策: リクエスト由来の tool_ids/tool_servers をユーザの許可ツール集合と突合し、許可外は拒否/無視。
- **GHSA-482j-2pq6-q5w4 (CVE-2026-45672, HIGH, <=0.8.11)** — code/execute が `ENABLE_CODE_EXECUTION=false` を無視。
  対策: コード実行 API 入口で当該フラグ(+ユーザ権限)を強制。**フォークの nsjail/Jupyter 実行系は要点検**。ゲートをルータ層で必ず評価。
- **GHSA-m8f9-9whg-f4xr (CVE-2026-45315, HIGH, <=0.9.2)** — `/api/v1/audio/transcriptions` がユーザ指定拡張子でファイル保存、`/cache/{path}` が Content-Type を推測配信→polyglot(WAV+HTML)で Stored XSS。**audio はフォーク保持機能**。
  対策: 保存時に拡張子をサーバ側で正規化(mime 判定)し、`/cache` 配信に `Content-Disposition: attachment` と安全な Content-Type を付与。`routers/audio.py` 確認済み。
- **GHSA-vjqm-6gcc-62cr (CVE-2026-54012, HIGH, <=0.9.5)** / **GHSA-vrhc-3fr6-pc3c (CVE-2026-54010, HIGH, <=0.9.5)** — 偽装した model meta.knowledge / chat-file リンクで**他ユーザファイルの読取・削除**。
  対策: ファイル参照解決時に file_id と要求ユーザの所有/共有権限を必ず突合。models の meta.knowledge、チャット添付リンクの両経路。
- **GHSA-r472-mw7m-967f (CVE-2026-45402, HIGH, <=0.9.4)** — Folder Knowledge/KB Attach で file_id 未検証→横断ファイルアクセス。対策同上(file_id 所有者検証)。
- **GHSA-4g37-7p2c-38r9 (CVE-2026-45398, HIGH, <=0.9.4)** — Retrieval API が KB アクセス制御をバイパス(IDOR)。対策: retrieval 経路で KB/collection のアクセス権を強制。
- **GHSA-8jjp-r2w2-4v22 (CVE-2026-45399, HIGH, <=0.8.12)** — 低権限ユーザがグローバル背景タスクを列挙/停止→全体のチャット妨害(DoS)。対策: task 列挙/停止 API に所有者/管理者スコープを適用。
- **GHSA-26g9-27vm-x3q8 (CVE-2026-45671, HIGH, <=0.8.12)** — 共有チャットの branch が access_type を無視→他人ファイル削除。対策: 共有チャット分岐でも書込/削除は access_type を評価。
- **GHSA-jxwr-g6r6-j3fx (CVE-2026-44569, HIGH, <=0.6.18)** — メッセージアクセスの認可破れ。対策: メッセージ更新/削除に**所有者検証**を追加(channel-level だけでなく message-level)。
- **GHSA-hmjq-crxp-7rjw (CVE-2026-44570, HIGH, <0.6.19)** — memories API の認可不整合。`routers/memories.py` 存在確認済み。対策: memories CRUD にユーザスコープ徹底。

### Tier 2: 認証/OAuth・SSRF・XSS(該当機能が有効なら高)

- 認証系: **GHSA-2r4p-jpmg-48f4(LDAP 空PW, CRITICAL)**, **GHSA-h3ww-q6xx-w7x3(CVE-2026-45675, LDAP/OAuth 初回ユーザ競合→複数管理者)**, **GHSA-57q6-fvp4-pqmm(CVE-2026-45339, x-api-key で制限エンドポイント回避)**, **GHSA-7rw5-9f7q-xj36(ログインタイミングでアカウント列挙)**。
- SSRF 系(RAG/web-fetch/画像・OAuth 画像取得): GHSA-24c9-2m8q-qhmh, GHSA-226f-f24g-524w, GHSA-rh5x-h6pp-cjj6(CVE-2026-45401), GHSA-8w7q-q5jp-jvgx, GHSA-c6xv-rcvw-v685(CVE-2025-65958), GHSA-82r6-c5jm-f3mw, GHSA-4v7r-f4w8-8972, GHSA-f776-fp4w-266c(※範囲外), GHSA-jgx9-jr5x-mvpv, GHSA-m69w-p7m4-585j。
  対策共通: `validate_url` にリダイレクト追従時の再検証、内向き IP/メタデータ IP のブロック、`WEB_FETCH_FILTER_LIST` のホスト境界一致。閉域配備でも SSRF は内部横展開に効くので RAG/画像取得を使うなら要対処。
- XSS 系(レンダリング面): GHSA-8gh5-qqh8-hq3x(CVE-2025-46571, HTML アップロード, =0.6.5), GHSA-w7xj-8fx7-wfch(Insert Prompt as Rich Text), GHSA-r29h-37fj-x2w6(SVG レンダラ), GHSA-xc8p-9rr6-97r2 / GHSA-vjm7-m4xh-7wrc(iFrame 埋め込み), GHSA-jwf8-pv5p-vhmc / GHSA-hcwp-82g6-8wxc(Excel/DOCX プレビュー {@html}), GHSA-v8qj-hxv7-mgvv(Mermaid), GHSA-gf5m-wcrh-7928(Model Description), GHSA-8wvc-869r-xfqf(Note PDF※notes 無→非該当), GHSA-cqp4-qqvg-3787(Banner), GHSA-6gh2-q7cp-9qf6(Profile Picture), GHSA-fq3v-xjjx-95rc(Pending User Overlay), GHSA-3vv5-8xxp-4f55(postMessage), GHSA-3wgj-c2hg-vm6q(OAuth picture SVG)。
  対策共通: `{@html}`/`.innerHTML`/SVG/iFrame 描画箇所を洗い出し DOMPurify を一貫適用、SVG・HTML アップロードは配信時に無害化 or attachment 化。

### Tier 3: 既定オフ機能・条件付き(RAG/Ollama/channels/Redis 等)

- RAG/KB 認可: GHSA-7r7x-gjvr-448g, GHSA-cx9v-4qj2-jrw6(CVE-2026-54016), GHSA-65pg-qhhw-mxwg, GHSA-6c2x-gcp3-gp73, GHSA-7r82-qhg4-6wvj, GHSA-h36f-rqpx-j5wx, GHSA-p5cp-r7rg-qpxc(Milvus), GHSA-26gm-93rw-cchf, GHSA-jjp7-g2jw-wh3j。
- Ollama 特化(フォークは OpenAI 互換一本 = 未使用想定): GHSA-9rpj-v7hf-vv2w, GHSA-rcvp-6fgw-c7fh。
- channels(既定オフ, コードは存在確認済み): GHSA-73x5-h92w-xc2j, GHSA-3856-3vxq-m6fc, GHSA-7rjh-px4v-5w55, GHSA-c7wp-3qh5-55pv, GHSA-hmgr-67hw-j2cq, GHSA-5gc6-xhv4-2wg6, GHSA-wwhq-cx22-f7vv, GHSA-jgj3-r8hr-9pjw, GHSA-gh7p-78x6-jw6m(※範囲外 >=0.7.0)。
- Redis/マルチインスタンス前提: GHSA-3x8w-4f7p-xxc2(tool_servers キャッシュ汚染; tool_servers は保持機能)。
- その他: GHSA-cache /path traversal **GHSA-j2c8-v969-8r5c(CVE-2026-54014, `/cache/{path}` sibling-prefix path traversal)** は audio/画像キャッシュ配信に効くので Tier2 相当で要注意。GHSA-4r4w-2wgp-w7cj(prompt history IDOR), GHSA-rjmp-vjf2-qf4g(feedback 偽装), GHSA-hr43-rjmr-7wmm(folder mass assignment), GHSA-mqq6-cqcx-38vg(model import 上書き), GHSA-j6w6-986j-2m2m(CSRF image url), GHSA-h2cw-7qw9-56xr/GHSA-jh9g-8jqw-m2qx(system prompt 露出)【2026-10-05 受容 — 上の「受容(対応しない)判断」】, GHSA-v6qf-75pr-p96m(model ACL query param), GHSA-74h3-cxq7-vc5q(Socket.IO session_id でツール実行; code-interpreter 部分は Pyodide 削除で緩和されるがツール実行面は残る)。

---

## 範囲外(v0.6.5 に非該当)19 件

| GHSA | 範囲 | 理由 |
|---|---|---|
| GHSA-frvj-c5qp-xj4w | >=0.9.6,<0.10.0 | 0.6.5 は範囲前 |
| GHSA-mvx4-532p-xfm9 | >=0.9.0,<0.10.0 | 同上 |
| GHSA-m3qf-58wf-w979 | >=0.8.12,<0.10.0 | 同上(Arena) |
| GHSA-2xwm-4h2q-ggfx | >=0.9.6,<0.10.0 | 同上 |
| GHSA-rqj7-6wrp-6g2g | >=0.8.11,<0.10.0 | 同上 |
| GHSA-3wp3-xxj9-5jqq | >=0.6.27,<0.10.0 | 0.6.5<0.6.27 |
| GHSA-855v-hq7w-jmjw | >=0.9.0,<0.10.0 | 範囲前 |
| GHSA-ffpj-xv5c-p3gw | >=0.9.2,<0.10.0 | 範囲前(skills) |
| GHSA-x2ff-v5v8-m75m | >=0.9.5,<0.10.0 | 範囲前 |
| GHSA-gh7p-78x6-jw6m | >=0.7.0,<0.10.0 | 0.6.5<0.7.0 |
| GHSA-gmfw-g93r-vg53 | >=0.6.16,<0.10.0 | 0.6.5<0.6.16 |
| GHSA-f776-fp4w-266c | <0.5.11 | 0.6.5 は修正後 |
| GHSA-gm54-m39w-grjp | <=0.5.6 | 0.6.5>0.5.6 |
| GHSA-4vrc-m9ch-6m3r | <0.6.5 (patched 0.6.5) | **0.6.5 で修正済み** |
| GHSA-r8wh-8m7r-fh33 | <=0.3.15 | 修正後 |
| GHSA-6xcp-7mpr-m7wm | <0.3.14 | 修正後 |
| GHSA-4vg5-rp28-gvjf | <=0.1.123 | 修正後 |
| GHSA-39wr-r5vm-3jxj | 0.1.111 | 修正後 |
| GHSA-9pgh-j74g-qj6m | <=0.1.123 | 修正後 |

## 範囲内だがフォークに機能が無く非該当(要再確認)

grep で作業ツリー(v0.6.5 ベース)に該当ルータ/機能が見つからなかったもの:

- **notes 機能なし**(notes ルータ/モデル無): GHSA-8788-j68r-3cgh, GHSA-jx2x-j75f-xq3j, GHSA-x3qm-p8hr-3c3h, GHSA-8wvc-869r-xfqf。GHSA-45m8-cpm2-3v65(Socket.IO 内 stale admin role; 影響実証は note だが根本の役割失効バグは残存しうる→**条件付き要確認**)。
- **terminal proxy 機能なし**(terminals ルータ無): GHSA-j657-m4c4-24jq, GHSA-r2wg-2mcr-66rv(CVE-2026-54017)。
- **calendar 機能なし**: GHSA-f3g7-59qc-pqg6(CVE-2026-54006)。
- **協調ドキュメント(ydoc/awareness)なし**: GHSA-vrfh-rj4q-rmhr(CVE-2026-44564)。
- **OpenAI Responses パススルー未実装**(openai.py に `/responses` ルータ無、`get_all_models_responses` は別物): GHSA-hp5m-24vp-vq2q(CVE-2026-44556)。
- **Pyodide 削除済み(issue #11)**: GHSA-4r2p-27mh-5m22(Same-origin Pyodide RCE)は**フォークでは成立しない**。GHSA-74h3(code-interpreter 経由)も Pyodide 部分は無効化、ただしツール実行面は残る。

> 注: notes/terminal/calendar が「本当に v0.6.5 で未実装」なのか「フォークで削除済み」なのかは feature-triage.md と照合して確定させること。少なくとも現作業ツリーには存在しない。

## 推奨アクション(issue #24 向け)

1. **即バックポート**: Tier 1 の TOP3(9f4f / 2r4p / 482j)+ CORE 認可系(9vvh/gfm2/p4fx/4pcg/7429)。いずれも既定オン面かつフォーク中核。
2. **audio 面**: m8f9 + `/cache/{path}` の j2c8 をセットで(STT はフォーク保持機能)。
3. **XSS 一括**: レンダリング/アップロード無害化を横断タスク化(9f4f, 8gh5, iFrame/SVG/Mermaid/Excel 群)。DOMPurify 適用箇所の棚卸しで多数を一度に潰せる。
4. **監視**: v0.10.2 の伏字宣言により未公開 access-control 修正が残る。GHSA ページを定期確認し `< 0.10.0` 帯の新規公開を追う(refs #24)。
5. RAG/web-search/channels/Ollama/OAuth 系は「既定オフのまま」を維持できるなら優先度を下げてよいが、有効化前提が変わる場合は Tier2/3 を再評価。
