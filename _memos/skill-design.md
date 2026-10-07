# Skill 設計(issue #35)

- 決定日: 2026-07-05(#35 の議論で確定)。改訂: 2026-07-06
  (発火の会話持続化・load_skill 命名確定・step2 の 2a/2b 分割・ハイライト要件 — 本文反映済み)、
  2026-10-03(入口 3 = 添付による自動ロードを追加)
- 位置づけ: upstream キャッチアップ(#18)ではなくその先。upstream に同等機能は無い
  (外形参考: v0.8.0 `$` コマンド / v0.10.0 オンデマンド manifest — 旧 `tools-catchup.md` §skills、
  #34 棚卸しで削除済み)

## 確定した設計

1. **Skill に一本化** — Prompt という概念・命名は廃止。テーブル・API・UI とも skill。既存 prompt データは移行
2. `/` でユーザーが呼び出せ、モデルも自律的に呼べる。**発火はサーバー実行の単一パス**
   (フロントのテンプレート全文挿入は廃止、picker は補完のみ)
3. Skill に Tool を紐づけ、**遅延評価**(発火時に初めて tool 群がロードされ、モデルに公開される)
4. 変数: ブラウザ専用変数({{CLIPBOARD}} 等)廃止。依存宣言リストも設けない。
   サーバーが自力で解決できる変数({{USER_NAME}} / {{CURRENT_DATE}} 等)を注入時に無条件展開するだけ。
   {{BASE_MODEL_ID}}(2026-10-03 追加)= 発火ターンのモデルが実際に推論する base の id
   (preset の base_model_id)。subagent の子ターンでは子 preset の値。モデル情報が無ければ
   "Unknown"。{{USER_ROLE}} / {{USER_GROUPS}}(2026-10-07 追加、llens 依頼)= role の値そのまま /
   所属グループ名(保存値)を ", " で連結、sort key は (casefold, 元の名前)、所属なしは空文字列、
   admin でも所属分のみ。グループ照会は {{USER_GROUPS}} が本文にあるときだけ prompt_template 1 回に
   つき 1 回。subagent の子ターンでも親と同じユーザー。一覧と定義は `utils/task.py`
   (prompt_template / resolve_base_model_id / user_group_names / user_prompt_params)が
   単一情報源で、preset のシステムプロンプトと共通
5. 引数機構は初期実装なし(将来 optional で {{PARAM}} = frontmatter 定義の呼び出し時パラメータ)
6. グローバル有効化トグルなし。制御面は**モデル(preset)設定に一元化**(#42 改訂):
   - モデル capability「スキル」(`meta.features.skills`、既定 ON)。OFF で manifest も
     `/command` 発火も全停止(CoT 無しモデル等)
   - `/` で発火する範囲 = **機能 ON ∧ preset `meta.skillIds` 紐づけ ∧ ユーザー read 可視**(AND)。
     旧「read 可視な skill 全部(どのモデルでも)」から #42 で変更 — モデルを唯一の制御面に
   - モデル manifest 露出 = 同じ 3 条件の AND
   - ~~group_terms 交差は read 可視の判定に含む~~ → **改訂(2026-07-10、#66)**: group_terms は
     撤去(複数 group 所属 + OR 合成で足りる)。read 可視 = user_ids / group_ids の OR のみ
7. **native function calling 限定**。非 native(prompt-based)tool 経路は llens から削除(#18)
8. 作成権限: **tool 紐づき skill は admin のみ**。一般ユーザーはテンプレート skill(content のみ)を作成可
   → **改訂(2026-07-10、#66)**: 作成できるのは skill editor(`workspace.skills`)のみで、
   **必ず私有**。詳細は下の「権限まとめ」と `permissions.md`

## データモデル

`skill` テーブル(新設。`prompt` から移行):

> **改訂(2026-07-07、#43 Phase 2a)**: 可読キーを **`id` 一本に統合**(旧 `command` 列を撤去)。
> `id` が `/command` 発火キー兼 PK(先頭 `/` なしで正規化・作成時確定・以後不変。改名は削除→再作成)。
> `title` → **`name`** にリネーム(全エンティティ {id, name, description} に統一)。
> 発火は `get_skill_by_id`、load_skill manifest / wire 引数は `skill_id`(旧 `skill_command`)、
> activation は `id`/`name`(旧 `command`/`title`)。以下の表は改訂後。migration `c5e7a1b9d2f6`。
> 旧「id=uuid と command を分離」方針(rename 耐性のため)は撤回 — rename は削除→再作成で扱う(#43)。
>
> **追記(2026-07-07、#43 Phase 2b 出荷 3404aae85 + モデル 53183f1f3)**: 可読 id の編集 UI を
> skill/tool/function **+ Model** の編集画面に横断追加(Model も出荷済み=後回し解消)。
> 編集時も id を input 可にし、id が変わったら ConfirmDialog「Change ID?」を出して
> **旧 id 削除 → 新 id 作成**で差し替える(PK 不変・既存 create+delete API 再利用で backend 変更なし)。
> 参照(skillIds/toolIds/filterIds/actionIds)と valves は #46 以降 存在しない id を無視するため
> **静かにちぎれる=リセット**(誠実仕様)。create が 400(id 衝突/不正)なら旧を消さず中断。
> tool/function は差し替え=create 経由で description 必須(#43 P1)のため、空 description の rename は
> confirm 手前で toast 停止(skill は description 任意で対象外)。id/name の空は input native の required 警告。
> 併せて skill 本文を CodeEditor(markdown)化・Save ボタン左下・tool/function の
> 「arbitrary code execution」警告文言と作成時の acknowledge ダイアログを撤去(作成は確認なしで即保存)。
> Model は valves 無しのため confirm から valves 行を省く(参照ちぎれ 1 行のみ)。model の
> recreate は既存チャットの model 参照をちぎる(誠実仕様)。実機で skill(kansai)/tool
> (render_probe 往復)/model(llens-assistant 往復)を検証済み。
> 残: 新規 i18n キー 5 件の locale 追従、dev fixture `vault`(uuid のまま)の id リネーム。

| カラム | 型 | 備考 |
|---|---|---|
| `id` | String PK | `/command` 発火キー(先頭 `/` なしで正規化)。人が付ける・作成時確定・不変 |
| `user_id` | String | 作成者 |
| `name` | Text | 表示名(旧 title) |
| `description` | Text, nullable | モデル向け manifest 用。空ならモデルには露出させない(name で代用しない — 説明の無い skill をモデルに見せても選べない) |
| `content` | Text | 指示本文。サーバー既知変数の展開対象 |
| `tool_ids` | JSON, default [] | 紐づく tool。非空の作成/更新は admin 必須 |
| `auto_load_content_types` | JSON, nullable | 自動ロード条件(入口 3)。content_type の前方一致リスト(例 `["audio/"]`)。空 = 自動ロードしない。保存時に小文字化・空要素と重複の除去。migration `d2a6f4b81c3e` |
| `access_control` | JSON, nullable | None=公開 read / {}=私有 / read.group_ids・user_ids のみ(**#66 で write 側と group_terms を撤去** — 真実源 `permissions.md`) |
| `created_at` / `updated_at` | BigInteger | prompt の単一 timestamp から正規化 |

移行(Alembic):
- `prompt` 全行 → `skill`(command の `/` strip、timestamp → created/updated 両方に複写)
- 権限キー `workspace.prompts` → `workspace.skills`、`sharing.public_prompts` → `sharing.public_skills`:
  **config.py の DEFAULT_USER_PERMISSIONS だけでなく、`group` テーブル各行の `permissions` JSON と
  PersistentConfig(DB 内 `user.permissions`)の rename も移行スクリプトに含める**
- prompts API / workspace UI は廃止(router 削除、フロントは skills に置換)

## 発火フロー

発火処理は 1 つ(middleware に新設 → #23 で `nucllei/utils/skills.py` へ分離済み、2026-07-14):
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
   - **冗長ロードの歯止め(2026-10-05、refs #142 監査「同じ内容が重複して context を食う」)**:
     上は期待で、実装でも止める。`/command` は同じ skill が既にそれより前に載っていれば
     (先行する `/command` の注入 / 先行履歴の load_skill)exchange を足さない。
     `load_skill` は読み込み済み(build 時に渡された messages に本文がある / 同じターン内で
     既に読んだ)なら本文を返さず `LOADED_RESULT`(「skill X は読み込み済み (…)。次の手: 上の
     手順書に従って作業を続ける。」)を返す。tool の遅延取り込みは冪等なのでそのまま行う。
     ※ messages の受け渡し(`build_load_skill_tool(..., messages=form_data["messages"])`)は
     middleware 側の結線が要る — 未結線の間は「同じターン内の 2 回目」だけが効く
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

### 入口 3: 添付による自動ロード(2026-10-03 追加、ken 決定)

**なぜ**: 入口 1(利用者の `/command`)と入口 2(モデルの判断)だけでは、「音声が添付されたら
音声の扱い方が必ずモデルに入っている」を保証できない(モデルが添付を見て load_skill するかは
モデル次第)。マイクの音声入力が録音を添付として残すようになり(`_memos/audio.md`)、録音の付いた
発言でモデルが最初から扱い方を把握している必要が出た。音声専用にはせず、**条件は skill 側が
宣言する**汎用の仕組みにした(配備層の `audio` skill が「音声の添付」を条件に書く)。

**条件の宣言**: skill の `auto_load_content_types`(content_type の**前方一致**のリスト、
例 `["audio/"]`)。拡張子では判定しない(録音は Chrome が `audio/webm`、Safari が `audio/mp4`)。
`tool_ids` と同じ「skill 行の列」に置き、API のフォーム・workspace の編集画面(「自動ロード」欄、
カンマ区切り)から編集する。編集権限は skill 本体と同じ(tool 紐づき skill は従来どおり admin 専有)。

**発火**(`utils/skills.py::apply_skill_auto_load`。`process_chat_payload` で `/command` の発火と
`files` の読取権限フィルタの後に呼ぶ):

1. 候補 = モデルの `meta.skillIds` のうち、条件を宣言していて利用者に read 可視な skill
   (機能「スキル」OFF なら候補なし)。**認可は `/command` と同じ AND** をその場で確認し、
   欠ければ何もしない(エラーにしない)
2. 添付の content_type を集める。出どころは 2 つ:
   - リクエスト直下の `files`(チャットの添付一覧)。**チャット wire では画像以外の添付は
     メッセージに載らず**、この一覧として毎リクエスト届く(FE の `chatFiles`。実リクエストで確認)
   - user メッセージの `image_url` パート(画像。内部 file URL は DB、data URL は MIME)。
     tool 結果の画像を運ぶ user メッセージ(tool メッセージの直後)は利用者の添付ではないので除く

   content_type は**必ず DB の値**(`file.meta.content_type`)で、読取権限のある file だけを見る
   (クライアントが申告した meta は信用しない)
3. 条件に合えば `/command` と同じ合成 exchange(assistant の `load_skill` 呼び出し + tool result)を
   注入し、tool_ids の取り込み・result への tool 名の書き戻し・UI の seed は `/command` と同じ
   経路(`skill_activations`)に合流する。call id は `skill:<id>:auto`

**持続**: 状態は持たない。添付は毎リクエスト payload に載って来る(`files` はチャット単位、画像は
メッセージ内)ので、発火は毎リクエスト再導出され、添付の無い後のターンでも tool が載り続ける
(「会話履歴が唯一の状態」の原則どおり。サーバ駆動の続きターンも chat の `files` を渡すので同じ)。
本文・変数は `/command` と同じく毎リクエスト再展開。

**二重発火の扱い**: 履歴に同じ skill の `load_skill`(`/command` の合成 exchange・モデル自律の
呼び出し)が既にあれば足さない。条件に合う添付が複数あっても 1 skill 1 回。複数の skill が
同じ添付に反応したらすべて発火し、順序は `meta.skillIds` の順。

**位置**:
- 画像(メッセージに載る)= 最初に条件に合った user メッセージの直後(`/command` と同じ考え方)
- チャットの添付一覧 = **最初の user メッセージの直後**。wire が「どの発言に付いたか」を運ばない
  ため。添付の本文(`<source>`)が system メッセージに載る = チャット全体に効く、のと同じ扱いで、
  位置がターンをまたいで動かない(prefix キャッシュが壊れない。添付が付いたターンは system
  メッセージが変わるので、そのターンの再計算は元々避けられない)
- 同じ位置に `/command` の exchange があればその後ろ

**UI のカード**: 「添付が付いたターン」の応答にだけ load_skill カード(synthetic)を seed する。
画像は payload だけで分かる(合った user メッセージが最後の user メッセージか)。チャットの
添付一覧は payload から分からないので、**カードを出すかどうかだけ** chat DB(いま応答する
user メッセージの `files`)を見る。発火・認可・tool の有効化は payload だけで決まり、DB の
この参照には依存しない(chat 文脈の無い headless では発火するがカードは無い — 出す先も無い)。

**既知の割り切り**:
- 後のターンで添付したとき、合成 exchange は「最初の発言の直後」に現れる(モデルから見ると
  会話の頭で skill がロード済みだったことになる)。厳密に「添付した発言の直後」に置くには
  FE が user メッセージごとの添付を wire に載せる必要がある(`chat-messages.ts` の変更)
- 自動ロード済みの skill を後から `/command` で打つと、以後は `/command` 側の exchange に
  置き換わる(位置が 1 回動く)
- ブラウザのファイル選択で `.webm` の音声を添付すると content_type は `video/webm` になり、
  `audio/` には合わない(録音経路は `audio/webm` を明示して upload するので合う)

回帰テスト: `nucllei/test/apps/webui/test_skill_auto_load.py`。

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
  どちらも履歴からステートレスに再導出できる。入口 3(自動ロード)は payload の添付から
  同じくステートレスに再導出する(上記「入口 3」)
- #42 改訂: `/command` の発火は「機能 ON ∧ モデル紐づけ ∧ read 可視」の AND が前提
  (機能 OFF / 未紐づけの skill は生テキストで素通し)。詳細 `_memos/builtin-tools.md`
- 複数 skill は**和集合**。同一 skill の再発火は冪等(実装: 2 回目以降の `/command` は exchange を
  足さず発火リストにも載せない = その回の発火カードも出ない。load_skill の再ロードは短い結果 — 上記入口 1)
- 変数({{CURRENT_DATE}} 等)は**毎リクエスト再展開**(「発火時点」への固定は状態が要るので採らない。
  数ターンのドリフトは許容)
- skill content の後日編集は `/command` 発火分の**モデル向け注入には遡って効く**
  (合成 exchange を毎リクエスト再導出するため)。一方、UI に永続化された過去ターンの
  tool_calls ブロック表示と、モデル自律 load_skill の結果(履歴に凍結)は遡らない —
  既知の非対称として許容・明記

## 権限まとめ

**改訂(2026-07-10、#66 権限3層化 — 真実源は `permissions.md`)**: skill editor は
「私有ノート + 全体閲覧」モデル(editor が勝手に public skill を作れないこと)。

| 操作 | ゲート |
|---|---|
| skill 作成 | `workspace.skills`(= skill editor)。**必ず私有(ACL={} をサーバー強制)** |
| skill 編集・削除 | **owner のみ**(editor でも他人のは不可)。admin は全部 |
| ACL(公開・共有範囲)の編集 | **admin のみ**(editor の保存では既存 ACL を温存) |
| tool 紐づき skill(tool_ids 非空) | 全操作 **admin のみ**(付与・剥奪・削除とも) |
| 管理一覧の閲覧(中身込み) | editor は全件(`/skills/list`) |
| `/` 起動・モデル露出 | access_control read(user_ids / group_ids の OR。group_terms は撤去) |
| ~~公開共有(`sharing.public_skills`)~~ | 廃止(#66 で sharing.* ごと撤去) |

## 実装ステップ

### step1 — skill 一本化(サーバーサイド実行化)

1. [x] **(backend done)** `skill` テーブル + Alembic 移行
   - モデル: `nucllei/models/skills.py`(`Skill` / `SkillModel` / `SkillForm` /
     `SkillUserResponse` / `SkillsTable`)。
   - 移行: `nucllei/migrations/versions/f8a4d0e2c1b7_add_skill_table.py`
     (head `3781e22d8b01` から連結)。prompt 全行を skill へ複写(`/` strip、timestamp→
     created/updated、id=uuid、description=null、tool_ids=[])。複写ロジックは
     `copy_prompts_to_skills(conn)` に分離してテストから直接駆動。
   - **prompt テーブル・権限キーの rename は未実施**(移行方針の変更、下記参照)。
2. [x] **(backend done)** skills router `nucllei/routers/skills.py`(`/api/v1/skills`)。
   CRUD / admin ゲート(tool_ids 非空は admin 専用) / access_control。
   **旧 prompts router は残す**(移行方針の変更、下記参照)。
   権限キー `workspace.skills` / `sharing.public_skills` を `config.py` に追加(prompts は据置)。
3. [x] **(backend done)** middleware inlet: `/` パース → activate(tool なし。変数展開は
   `task.py` の `prompt_template` 流用)。実体は
   `nucllei/utils/middleware.py::apply_skill_command`
   (呼び出しは `process_chat_payload` 冒頭)。tool_ids 遅延評価の seam は同ファイルの
   `# TODO(#35 step2): tool_ids 遅延評価のフック` コメント位置。
   **→ 現在は #23(2026-07-14)で skill 発火一式(apply_skill_command / activate_skill /
   build_load_skill_tool)を `nucllei/utils/skills.py` へ分離済み。**
4. [x] フロント: picker 補完化(skills API 参照)、workspace UI を skills に置換、
   **有効 command のハイライト**(入力欄 + 送信済みメッセージ。サーバと同一述語)— 出荷 74f0e5161。
   prompts は同コミットで全退役(router/model/UI/権限キー削除、prompt テーブル drop = c2d9a7b41e05。
   データ移行なし — ユーザー決定 2026-07-06)。
5. [x] **(backend done)** 回帰テスト: `nucllei/test/apps/webui/test_skills.py`
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
   (tool_ids 消費は step2b で実装済み — `/` 側は payload 合流、load_skill 側は
   `load_tools` フック。下記 step2b 参照)
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
4. [x] C トラック分も完了(2026-07-06 同日):
   - get_tools 同期ロードの async 化 + sync tool のワーカースレッド実行(#28 前半、5a3e815a6)
   - 未知 tool call の graceful 処理(#18-2、df9408ebd)
   - クロスターン tool 履歴の復元(#18-2、f62deb49b — `expandAssistantToolHistory`。
     合成 exchange は snapshot の synthetic マーカでスキップし inlet 再注入と棲み分け)
   - finish_reason 整合ガード(#18-2、c81926c5b)
   残: 実挙動 fixture キャプチャ(#29 基盤)。
   ~~nsjail 自前ツール E2E(#18-3)~~ → **完了(2026-07-06、394b08701)**: E2E 8/8 PASS、
   基盤バグ 3 件(private spec 露出 / Optional anyOf KeyError / __chat_id__ 未注入)を
   修正。詳細 `_memos/builtin-tools.md`
5. [x] load_skill の戻り値を構造化(ユーザー提案 → 出荷 2026-07-06):
   両入口とも tool result = JSON `{"result": <展開済み content>, "tools": [<導入された
   tool 関数名>]}`。render ディスクリプタに新 kind `skill` を追加(#20 の枠組みのまま)し、
   UI は result を **markdown ハイライト**(HljsCode)+ tools を chip 一覧で描画。
   形が合わない実値は json フォールバック(他 kind と同じ規律)。
   `/command` 側は inlet 注入時に tools:[] で置き、payload の skill tools ロード後に
   実際の関数名を書き戻す(result_message は messages 内の同一 dict 参照)

### 前提・並行作業(実装順の根拠は #16 コメント参照)

- **先行**: legacy tool 経路(`chat_completion_tools_handler`)の削除(#18)— middleware が単純になり全作業が楽になる
- **step2 の前**: tool call ストリーミング堅牢化 + tool ロード async 化(#18/#28 のチャット経路分)
  — 遅延評価は native ループへの動的 tools 追加なので、ループが固まってから載せる方が手戻りがない
- レンダリング刷新(#17〜)は tool 基盤の後(ユーザー決定 2026-07-05)
