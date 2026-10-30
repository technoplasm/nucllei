# レシピ (利用者が残す手順) — nucllei の builtin へ移設 (2026-10-30)

- 依頼: llens (ken 指示「ぜんぶ nuc にやらせて」)。それまでは llens の jail のコマンド
  `/ext/bin/recipe` (list / check / promote) と prompt の「レシピ」節が持っていた。
- 仕様の単一情報源は README_API §6.5。ここは「なぜそうしたか」と割り切りだけ。

## 構成

| 何 | どこ |
|---|---|
| 判定 (名前・frontmatter・on_load・患者データ・層) | `nucllei/utils/recipes.py` (I/O なし・env.py を import しない) |
| 一覧 `{{USER_RECIPES}}` と「壊れている」の材料集め | `nucllei/utils/recipe_index.py` |
| tool (load_recipe / save_recipe / edit_recipe) | `nucllei/builtin_tools/recipe_tools.py` (`sandbox.Tools` の mixin = 同じ toolkit・ACL) |
| 後続 (on_load) の実行 | `utils/skills.make_followup_drain` (load_skill と共通。書いた順・深さ優先) |
| sandbox | `/recipes/index` `/recipes/folder` `/files/stat_many`、`/files/trash` の `name` |
| ホストの lint | `nucllei recipe-check` (共有の層だけ) |

## 決めたこと (と理由)

- **判定は nucllei に 1 つ。** llens の lint は同じ image で `nucllei recipe-check` を流す提案
  (README_API §6.5)。判定を 2 か所に書くと、片方だけ直る。配備層の知識 (使える人が限られる関数・
  由来メモのキー) は env `RECIPE_RESTRICTED_TOOLS` / `RECIPE_MEMO_KEYS` で受け、既定は空。
- **「壊れている」「書き込みの規則」「警告」の 3 つに分ける** (ken 2026-10-30 の最終形。「依存を
  消しちゃうのはよくあるので、warn で実行はできるが警告がモデルに入る。勝手に回復するチャンスを
  与える」)。
  - 壊れている = **frontmatter そのものの不正だけ** (`frontmatter_problems`: 閉じていない・YAML・
    知らないキー・必須キー・型・on_load の記法)。ERROR、本文も on_load も出さない。save / edit の
    「frontmatter の規則」もこれだけ。知らないキーも壊れている (`onload:` の綴り違いに気づけるように。
    文に使えるキーと近い綴り)。from_path の写し元の知らないキーも断る (組み直すと黙って落ちるので)。
  - 書き込みの規則 = 旧 check 由来 (患者データ・見えないパス・共有の層に置けない関数・symlink・
    入れ子・updated が未来・tools の使い残し)。save / edit は断る、lint は stop。
  - 警告 = frontmatter としては正しいが中の値が通らない (層の規則の違反 = `layer_warnings`、参照先が
    無い・読む人に見えない・使えない関数 = `reference_warnings`)。load_recipe は本文を返し、通らない
    on_load の要素は**その位置の後続の結果** (call id はそのまま、`error_kind: outcome`) に警告 1 行を
    入れて実行しない。save / edit は書いて返答に警告。lint は note。保存しない — 戻せば消える。
- **on_load の参照の層の規則** (llens 依頼・ken 2026-10-30): 参照は狭い層 → 広い層の向きだけ。
  共有のレシピの hook で読む人の /workspace (記憶・EMR の書き出し) を読ませる注入を塞ぐ。共有の
  shell / python・共有 → 個人・グループ同士・全体 → グループは**警告で実行しない** (壊れているにしない
  — 実行しないことで塞ぐ)。違反した参照は stat もしない。既存の「見えないパス」(共有の層の
  `/workspace/recipes|imports|exports|trash|memory`) と重なる所は書き込みの規則を残す (= 書けない)。
  共有で付属を動かしたい人は個人のレシピに置いて recipe: で共有を読む。
- **「使えない」の所属は {{USER_GROUPS}} と同じ** (管理者でも所属で見る — 0c6de9e14 と揃える)。
  ただしそのレシピ自身のグループの参照は除く (管理者が所属していないグループのレシピを load_recipe
  できるのは今までどおりで、中の参照だけ使えない、にしない)。tools の「手元に無い関数」は
  RECIPE_RESTRICTED_TOOLS の所属で見る (そのターンの tool の一覧は見ない — 一覧は tool が決まる前に
  作り、skill の on_load で後から tool が増えるので、当てにならない)。表で null の関数は判定しない。
- **一覧の判定は軽く** (形・frontmatter・on_load)。患者データと関数の層は書く前の
  `check_folder` だけ。一覧は毎リクエスト走るので、問い合わせは sandbox 2 回 (index + stat_many) +
  DB (所属 1 + skill 1) に固定。聞けなかった参照先は「分からない」で、警告しない。
- **edit_recipe / save_recipe は今の状態で断らない** (ken)。壊れたレシピを直す入口だから。
- **レシピの on_load は短い記法だけ**、要素はすべて実際の tool 呼び出し (`skill:` = load_skill、
  `recipe:` = load_recipe …)。モデルが自分で呼べる範囲を超えない (skill の on_load の load_skill
  要素のような合成 exchange にはしない — 認可の抜け道を作らない・保存されて 2 回目は動かない)。
- **shell / python は付属だけ** (ken 未回答の点を固い側で)。hook はモデルの判断を通らず読んだ人の
  jail で動くので、検査できる範囲に閉じる。引数は 1 つずつ quote、python も run_shell 経由
  (stateful kernel に他人のコードを流さない)。緩めるのは後で。
- **from_path はパスを書き換えない** (付属は相対で指す決まり)。旧 promote で書き換えた
  `/workspace/recipes/<名前>/` は、共有の層では「見えないパス」で止まる。
- **{{USER_RECIPES}} は非同期の入口で先に取る** (ContextVar、1 リクエスト 1 回)。`prompt_template`
  は同期のまま。取っていない経路では `(レシピの一覧はここでは取れない)` + warning。

## 割り切り・未了

- ファイルブラウザ (FE) で RECIPE.md を直接書くのは止めていない (検査が掛からない。壊れれば一覧に出る)。
- FE の skill 編集画面の保存前チェックは `load_skill` 以外を管理者専用として扱っているので、
  editor が `load_recipe` / 短い記法を書くと画面側で止まる (サーバは受ける)。FE の追従は未。
- レシピの `skill:` は load_skill の manifest (そのモデルの skillIds ∩ 説明あり) にあるものだけ動く。
  一覧の警告は read 可視だけを見るので、manifest に無い skill は読み込み時に ERROR 1 行。
- on_load の `recipe:` の先が壊れている・警告付きなのは、読む側の判定に含めない (先の load_recipe が
  その位置で自分の ERROR・警告を返す)。
- 警告で飛ばした要素もその chat では「済み」になる (後で参照先を戻しても、同じ chat では動かない)。
- ホストの lint の対象は共有の層だけ。個人の /workspace は点検しない (ken)。
- 実機 (コンテナ) は未確認 — sandbox の image の更新が要る。
