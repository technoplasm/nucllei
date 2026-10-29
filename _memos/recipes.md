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
- **「壊れている」は保存しない** (ken 2026-10-30)。一覧と load_recipe のたびに `assess_recipe` で
  判定する。参照先を戻せばレシピ側は何もしなくても直る。一覧と load_recipe は同じ関数を通す。
- **一覧の判定は軽く** (形・frontmatter・on_load の参照先だけ)。患者データと層は書く前の
  `check_folder` だけ。一覧は毎リクエスト走るので、問い合わせは sandbox 2 回 (index + stat_many) +
  DB 1 回に固定。聞けなかった参照先は「分からない」で、壊れているとは言わない。
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
  一覧の「壊れている」は read 可視だけを見るので、manifest に無い skill は読み込み時に ERROR 1 行。
- ホストの lint の対象は共有の層だけ。個人の /workspace は点検しない (ken)。
- 実機 (コンテナ) は未確認 — sandbox の image の更新が要る。
