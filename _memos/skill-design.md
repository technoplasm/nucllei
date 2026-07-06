# Skill 設計(issue #35)

- 決定日: 2026-07-05(#35 の議論で確定)。改訂: 2026-07-06
  (発火の会話持続化・load_skill 命名確定・step2 の 2a/2b 分割・ハイライト要件 — 本文反映済み)
- 位置づけ: upstream キャッチアップ(#18)ではなくその先。upstream に同等機能は無い
  (外形参考: v0.8.0 `$` コマンド / v0.10.0 オンデマンド manifest — `tools-catchup.md` §skills)

## 確定した設計

1. **Skill に一本化** — Prompt という概念・命名は廃止。テーブル・API・UI とも skill。既存 prompt データは移行
2. `/` でユーザーが呼び出せ、モデルも自律的に呼べる。**発火はサーバー実行の単一パス**
   (フロントのテンプレート全文挿入は廃止、picker は補完のみ)
3. Skill に Tool を紐づけ、**遅延評価**(発火時に初めて tool 群がロードされ、モデルに公開される)
4. 変数: ブラウザ専用変数({{CLIPBOARD}} 等)廃止。依存宣言リストも設けない。
   サーバーが自力で解決できる変数({{USER_NAME}} / {{CURRENT_DATE}} 等)を注入時に無条件展開するだけ
5. 引数機構は初期実装なし(将来 optional で {{PARAM}} = frontmatter 定義の呼び出し時パラメータ)
6. グローバル有効化トグルなし。**「有効化」という独立状態を持たず、可視性 = access_control に一本化**:
   - `/` で呼べる範囲 = ユーザーが read 可視な skill 全部(group_terms 交差を含む)
   - モデル manifest 露出 = model preset の `meta.skillIds` ∩ ユーザー可視
7. **native function calling 限定**。非 native(prompt-based)tool 経路は llens から削除(#18)
8. 作成権限: **tool 紐づき skill は admin のみ**。一般ユーザーはテンプレート skill(content のみ)を作成可

## データモデル

`skill` テーブル(新設。`prompt` から移行):

| カラム | 型 | 備考 |
|---|---|---|
| `id` | String PK | uuid。旧 prompt は command が PK だったが rename に耐えるよう分離 |
| `command` | String, unique | **先頭 `/` なしで正規化**して格納(旧 prompt は `/` 込み — 移行時に strip) |
| `user_id` | String | 作成者 |
| `title` | Text | 表示名 |
| `description` | Text, nullable | モデル向け manifest 用。空ならモデルには露出させない(title で代用しない — 説明の無い skill をモデルに見せても選べない) |
| `content` | Text | 指示本文。サーバー既知変数の展開対象 |
| `tool_ids` | JSON, default [] | 紐づく tool。非空の作成/更新は admin 必須 |
| `access_control` | JSON, nullable | 既存規約どおり(None=公開 read / {}=私有 / group_ids・user_ids・group_terms) |
| `created_at` / `updated_at` | BigInteger | prompt の単一 timestamp から正規化 |

移行(Alembic):
- `prompt` 全行 → `skill`(command の `/` strip、timestamp → created/updated 両方に複写)
- 権限キー `workspace.prompts` → `workspace.skills`、`sharing.public_prompts` → `sharing.public_skills`:
  **config.py の DEFAULT_USER_PERMISSIONS だけでなく、`group` テーブル各行の `permissions` JSON と
  PersistentConfig(DB 内 `user.permissions`)の rename も移行スクリプトに含める**
- prompts API / workspace UI は廃止(router 削除、フロントは skills に置換)

## 発火フロー

発火処理は 1 つ(middleware に新設、将来 #23 で module 分離):
**activate(skill, ctx) = content の変数展開 → 会話への注入 + tool_ids があれば `get_tools()` でロードして
その先のモデル呼び出しの tools 配列へ追加**

### 入口 1: ユーザー起動(`/`)

1. フロント: picker は補完のみ(`/command` トークンを挿入するだけ)。メッセージは生のまま送信。
   **有効な command だけハイライト**する(サーバと同一述語: 先頭行行頭 token ∈ read 可視 skill の
   command。無効は普通のテキスト表示 = サーバの素通しと見た目が一致)— frontend batch の要件
2. middleware inlet: ペイロード内の**全 user メッセージ**について先頭行の行頭
   `/([a-zA-Z0-9_-]+)` を検出 → read 可視な skill を command で lookup
   (毎リクエスト全走査 = 発火の会話持続。下記「発火の有効範囲」)
3. ヒット: **load_skill を呼んだことにする**(2026-07-06 改訂 — トークン置換案を撤回)。
   user メッセージは生のまま残し、直後に合成の assistant tool_call(load_skill)+
   tool result(展開済み content)を注入して生成を開始する。
   - モデル自律起動(入口 2)と wire 上も完全対称。モデルは「ロード済み」を履歴で
     知るので冗長ロードが起きない(manifest からの除外ルールは不要 — 撤廃済み)
   - UI は既存の tool_calls 描画(View Result from load_skill)がそのまま使える。
     当該ターンの発火は synthetic マーカ付き tool_calls ブロックとして応答先頭に
     seed する(モデル向け履歴へは再送しない — 実体は messages 注入済み)
   - (step2b で)tool_ids ロード → tools 配列へ
4. ミス: 何もしない(通常テキストとして素通し。エラーにしない)

### 入口 2: モデル自律起動(manifest 経由)

1. リクエスト組み立て時、preset `meta.skillIds` ∩ ユーザー可視 ∩ description 有り の
   skill 群から**単一の擬似 tool `load_skill(skill_command)`** を合成して tools 配列に常載
   (description に「command: description」一覧を列挙。skill ごとに擬似 tool を並べる案は
   tools 配列が skill 数に比例して肥大するため不採用)。
   ※ 発火済み skill の manifest 除外ルールは撤廃(2026-07-06): 入口 1 が合成 tool
   exchange を履歴に注入する方式になり、モデルは「ロード済み」を自然に知るため不要
2. モデルが `load_skill` を呼ぶ → native 実行ループ内で activate:
   tool result = 展開済み content(step2a)。tool_ids ロード → **次イテレーションの
   tools 配列に追加**(= 遅延評価)は step2b(下記の分割参照)
3. ループ継続。モデルは注入された指示と(step2b 以降)新しい tool を使って続行

命名は **load_skill 一本**(2026-07-06 確定)。現行 llens/upstream 外形の `view_skill` は採らない。
将来 tool ロードが加わっても「呼ぶと skill が有効になる」の意味論は不変なので rename も不要。
view_skill/load_skill の 2 本立ては「どちらを呼ぶべきか」をモデルに説明する羽目になる二重化で不採用。

### 発火の有効範囲(2026-07-06 改訂: 会話持続)

旧設計の「当該リクエスト内のみ有効(per-request 揮発)」は**撤回**(ユーザー要望に由来しない
実装都合の割り切りだった)。揮発だと `/command` を毎ターン打ち直す羽目になる上、
2 ターン目以降モデルに `/command` が生テキストで見える不整合も生む
(step1 実装の inlet は最後の user メッセージしか見ていない — 要修正、step1 残項目 6)。

発火は**そのメッセージ以降、その会話(現在のメッセージチェーン)で持続**する。実現はステートレス:
毎リクエスト、inlet が全 user メッセージを走査して発火を履歴から再導出する。会話そのものが唯一の
状態で、chat への active_skill_ids のような複製状態は持たない。分岐・編集の意味論も自然に正しくなる
(コマンドより前から分岐した枝では無効、メッセージを消せば失効)。

- 有効 skill の導出則(両入口で対称): チェーン内の `/command` ヒット ∪ `load_skill` 呼び出し。
  どちらも履歴からステートレスに再導出できる
- 複数 skill は**和集合**。同一 skill の再発火は冪等
- 変数({{CURRENT_DATE}} 等)は**毎リクエスト再展開**(「発火時点」への固定は状態が要るので採らない。
  数ターンのドリフトは許容)
- skill content の後日編集は `/command` 発火分の**モデル向け注入には遡って効く**
  (合成 exchange を毎リクエスト再導出するため)。一方、UI に永続化された過去ターンの
  tool_calls ブロック表示と、モデル自律 load_skill の結果(履歴に凍結)は遡らない —
  既知の非対称として許容・明記

## 権限まとめ

| 操作 | ゲート |
|---|---|
| テンプレート skill 作成/更新 | `workspace.skills`(admin バイパス) |
| tool 紐づき skill 作成/更新(tool_ids 非空) | **admin のみ**(新設ゲート。既存に admin 専用作成パスは無いので注意) |
| `/` 起動・モデル露出 | access_control read(group_terms 交差対応) |
| 公開共有 | `sharing.public_skills` |

## 実装ステップ

### step1 — skill 一本化(サーバーサイド実行化)

1. [x] **(backend done)** `skill` テーブル + Alembic 移行
   - モデル: `backend/open_webui/models/skills.py`(`Skill` / `SkillModel` / `SkillForm` /
     `SkillUserResponse` / `SkillsTable`)。
   - 移行: `backend/open_webui/migrations/versions/f8a4d0e2c1b7_add_skill_table.py`
     (head `3781e22d8b01` から連結)。prompt 全行を skill へ複写(`/` strip、timestamp→
     created/updated、id=uuid、description=null、tool_ids=[])。複写ロジックは
     `copy_prompts_to_skills(conn)` に分離してテストから直接駆動。
   - **prompt テーブル・権限キーの rename は未実施**(移行方針の変更、下記参照)。
2. [x] **(backend done)** skills router `backend/open_webui/routers/skills.py`(`/api/v1/skills`)。
   CRUD / admin ゲート(tool_ids 非空は admin 専用) / access_control。
   **旧 prompts router は残す**(移行方針の変更、下記参照)。
   権限キー `workspace.skills` / `sharing.public_skills` を `config.py` に追加(prompts は据置)。
3. [x] **(backend done)** middleware inlet: `/` パース → activate(tool なし。変数展開は
   `task.py` の `prompt_template` 流用)。実体は
   `backend/open_webui/utils/middleware.py::apply_skill_command`
   (呼び出しは `process_chat_payload` 冒頭)。tool_ids 遅延評価の seam は同ファイルの
   `# TODO(#35 step2): tool_ids 遅延評価のフック` コメント位置。
4. [x] フロント: picker 補完化(skills API 参照)、workspace UI を skills に置換、
   **有効 command のハイライト**(入力欄 + 送信済みメッセージ。サーバと同一述語)— 出荷 74f0e5161。
   prompts は同コミットで全退役(router/model/UI/権限キー削除、prompt テーブル drop = c2d9a7b41e05。
   データ移行なし — ユーザー決定 2026-07-06)。
5. [x] **(backend done)** 回帰テスト: `backend/open_webui/test/apps/webui/test_skills.py`
   (CRUD / command 一意性 / `/` 正規化 / admin ゲート / group_terms read フィルタ /
   移行複写 / `/` パース→注入)。
6. [x] inlet の**全 user メッセージ走査化**(会話持続 — 2026-07-06 改訂)— 出荷 0d82c77e8
   (activate_skill 切り出し・複数ターン回帰テスト込み)。**step1 完了**。

#### 移行期の判断(2026-07-05、step1 backend 実装時)

- **prompt テーブル・prompts router・prompts 権限キーは移行期間中そのまま残す**
  (旧フロントが prompts API に依存しており、両立させないと master が壊れるため)。
  当初案の「router 削除・権限キー rename・group/user permissions の rename」は
  **frontend batch(picker/workspace の skills 置換)と同時に実施**する。
- **picker 補完化と prompts 退役は frontend batch へ延期**(本 step は backend のみ)。
- **`tool_ids` カラムは新設済みだが runtime では未消費**。遅延評価(step2)の seam は
  `middleware.py::apply_skill_command` 内の `# TODO(#35 step2): tool_ids 遅延評価のフック`
  一箇所に集約。作成/更新 API では tool_ids 非空を admin 専用ゲートで受けるところまで実装済み。

### step2a — load_skill(content 返却のみ。C トラック非依存で先行可)— **出荷済み 0d82c77e8**

tool 遅延取り込み(tools 配列の動的変異 + async ロード)だけが実装コストの重い部分なので、
step2 を軽い前半(2a)と重い後半(2b)に分割する(2026-07-06 決定)。
実機検証済み(2026-07-06、SGLang × Kimi 実機 + Playwright): `/command` 発火・変数展開・
会話持続(履歴再展開)・picker 補完・ハイライト・load_skill 自律起動(厳密 JSON 引数)・
tool result = 展開済み content の全シーケンス。
**注意(dev 知見)**: preset の `meta.skillIds` を API で書き換えた場合、`app.state.MODELS`
キャッシュが古いままだと manifest に反映されない。`GET /api/models` を叩くと再構築される
(UI 経由なら自然に発生する既存挙動)。

1. `load_skill` 擬似 tool の合成(preset skillIds ∩ 可視。description に「command: description」一覧)
2. native ループ内で activate: tool result = 展開済み content を返すだけ(tools 動的追加はしない)。
   native ループに既にある tool 実行の 1 ケースに過ぎないので C を待たずに出荷できる
3. `/` 側の展開ロジックと**同一の `activate_skill(skill, user)` を経由**
   (seam は middleware.py の既存 TODO 位置。将来の tool_ids 消費はこの関数内 1 箇所に足す)
4. 教訓(現行 llens `scripts/owui/sync.py` より): manifest に id と name の両方を出すと
   モデルが name を渡して not found になる(現行は name=id 強制で回避)。
   llens の manifest 識別子は **command 一本**にする
5. 移行ノート: 現行 llens の skill md(`owui/skills/*.md`)は description に
   「必ず view_skill で本文をロード」と書いてある — フォーク移行時に load_skill へ文言書き換えが必要

### step2b — Tool 遅延評価(基本形 出荷済み 2026-07-06)

当初「C トラック後」としていたが、基本形は軽く実装できたため前倒しで出荷:

1. [x] `/command` 入口: payload 構築時に発火済み skill の tool_ids を集約し
   `get_tools(..., enforce_access_control=False)` でロードして tools に合流。
   会話持続 = 毎リクエスト再導出なので以降のターンでも載り続ける。
   **認可は skill の read 可視性が単位**(tool 紐づき skill は admin のみ作成可のため、
   per-tool の read 検証はスキップ。リクエスト由来 tool_ids の 4pcg ガードは従来どおり)
2. [x] load_skill 入口: `load_tools` フックで tools_dict(= metadata["tools"])と
   form_data["tools"] にループ内注入。両者は native ループが毎イテレーション参照する
   同一オブジェクトなので、**次のイテレーションからモデルに見える**
3. [x] 実機検証(SGLang × Kimi、秘密パスワード tool + /vault skill):
   手動 `/vault` → secret_password 呼び出し → 正答 / 自然文 → load_skill →
   注入された tool を次イテレーションで呼ぶ → 正答、の両方 PASS
4. 残(C トラック側): get_tools 同期ロードの async 化(#28)/
   履歴に残った tool call が現在の tools 配列に無い tool を指すケースの graceful 処理
   (skill 削除・権限剥奪・分岐で起きうる — #18-2)/
   実挙動 fixture キャプチャ(#29 基盤)/ nsjail 自前ツール E2E(#18-3)
5. 検討中(ユーザー提案 2026-07-06): load_skill の戻り値を string でなく
   {result, tools: [導入 tool 一覧]} に構造化し、何が入ったかをモデル/UI に見せる

### 前提・並行作業(実装順の根拠は #16 コメント参照)

- **先行**: legacy tool 経路(`chat_completion_tools_handler`)の削除(#18)— middleware が単純になり全作業が楽になる
- **step2 の前**: tool call ストリーミング堅牢化 + tool ロード async 化(#18/#28 のチャット経路分)
  — 遅延評価は native ループへの動的 tools 追加なので、ループが固まってから載せる方が手戻りがない
- レンダリング刷新(#17〜)は tool 基盤の後(ユーザー決定 2026-07-05)
