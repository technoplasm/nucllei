# Skill 設計(issue #35)

- 決定日: 2026-07-05(#35 の議論で確定)
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

1. フロント: picker は補完のみ(`/command` トークンを挿入するだけ)。メッセージは生のまま送信
2. middleware inlet: ユーザーメッセージ**先頭行の行頭** `/([a-zA-Z0-9_-]+)` を検出
   → read 可視な skill を command で lookup
3. ヒット: メッセージ中の `/command` トークンを展開済み content で置換(旧テンプレート挿入と同じ意味論を
   サーバー側で再現。後続テキストはそのまま残る)+ tool_ids ロード → tools 配列へ
4. ミス: 何もしない(通常テキストとして素通し。エラーにしない)

### 入口 2: モデル自律起動(manifest 経由)

1. リクエスト組み立て時、preset `meta.skillIds` ∩ ユーザー可視 の skill 群から
   **単一の擬似 tool `load_skill(skill_command)`** を合成して tools 配列に常載
   (description に「command: description」一覧を列挙。skill ごとに擬似 tool を並べる案は
   tools 配列が skill 数に比例して肥大するため不採用)
2. モデルが `load_skill` を呼ぶ → native 実行ループ内で activate:
   tool result = 展開済み content、tool_ids ロード → **次イテレーションの tools 配列に追加**(= 遅延評価)
3. ループ継続。モデルは注入された指示と新しい tool を使って続行

### 発火の有効範囲(v1 の割り切り)

発火は**当該リクエスト内のみ**有効。次のユーザーターンでは tools 配列は素の状態に戻る
(会話履歴に load_skill の tool call が残るので、モデルは必要なら再度呼べる)。
「会話履歴から発火済み skill を再現して tools を維持する」は使用感を見てからの拡張とする。

## 権限まとめ

| 操作 | ゲート |
|---|---|
| テンプレート skill 作成/更新 | `workspace.skills`(admin バイパス) |
| tool 紐づき skill 作成/更新(tool_ids 非空) | **admin のみ**(新設ゲート。既存に admin 専用作成パスは無いので注意) |
| `/` 起動・モデル露出 | access_control read(group_terms 交差対応) |
| 公開共有 | `sharing.public_skills` |

## 実装ステップ

### step1 — skill 一本化(サーバーサイド実行化)

1. `skill` テーブル + Alembic 移行(prompt データ・権限キー rename 含む)
2. skills router(CRUD、admin ゲート、access_control)+ 旧 prompts router 廃止
3. middleware inlet: `/` パース → activate(まず tool なしで。変数展開は `task.py` 流用)
4. フロント: picker 補完化(skills API 参照)、workspace UI を skills に置換
5. 回帰テスト: CRUD / 権限(admin ゲート・group_terms)/ `/` パース→注入(fixture リプレイ)

### step2 — Tool 遅延評価

1. `load_skill` 擬似 tool の合成(preset skillIds ∩ 可視)
2. native ループ内 activate: content を tool result で返す + tools 動的追加
3. `/` 起動側も同じ activate で tools が載ることを確認
4. SGLang × Kimi K2.6 実挙動キャプチャ(#29 基盤): load_skill 発火 → tool 追加 → 呼び出しのシーケンス
5. 自前ツール(nsjail+IPython / de-id)が skill 経由で動く E2E

### 前提・並行作業(実装順の根拠は #16 コメント参照)

- **先行**: legacy tool 経路(`chat_completion_tools_handler`)の削除(#18)— middleware が単純になり全作業が楽になる
- **step2 の前**: tool call ストリーミング堅牢化 + tool ロード async 化(#18/#28 のチャット経路分)
  — 遅延評価は native ループへの動的 tools 追加なので、ループが固まってから載せる方が手戻りがない
- レンダリング刷新(#17〜)は tool 基盤の後(ユーザー決定 2026-07-05)
