# レシピ (利用者が残す手順) — nucllei の builtin

- 仕様の単一情報源は README_API §6.5。ここは決めたことと割り切りだけ。

## 構成

| 何 | どこ |
|---|---|
| 判定 (名前・frontmatter・requires・患者データ・層) | `nucllei/utils/recipes.py` (I/O なし・env.py を import しない) |
| 一覧 (load_recipe の説明に入る) と「壊れている」「警告」の材料集め | `nucllei/utils/recipe_index.py` |
| tool (load_recipe / save_recipe / edit_recipe)、requires の展開、能力「レシピ」の合成 | `nucllei/builtin_tools/recipe_tools.py` (`build_recipe_tools` — middleware が load_skill の隣で差し込む。中に sandbox.Tools を持って HTTP・書き先・read_file / run_shell を借りる) |
| sandbox | `/recipes/index` `/recipes/folder` `/files/stat_many`、`/files/trash` の `name` |
| ホストの lint | `nucllei recipe-check` (共有の層だけ) |

## 決めたこと

- **判定は nucllei に 1 つ。** 配備側の lint は同じ image で `nucllei recipe-check` を流す
  (README_API §6.5)。判定を 2 か所に書くと片方だけ直る。配備層の知識 (使える人が限られる関数・
  由来メモのキー) は env `RECIPE_RESTRICTED_TOOLS` / `RECIPE_MEMO_KEYS` で受け、既定は空。
- **スキーマ**: 必須 title / description / author / updated、任意 `requires` だけ。使える人が限られる
  関数は RECIPE.md の文に名前が出るかで見る (語の境界で当てるだけ、説明に書いただけでも当たる厳しい側)。
  requires の要素は recipe / read / shell / python の 4 つ。`skill:` は書けない (skill を読み込むかは
  モデルの判断)。
- **「壊れている」「書き込みの規則」「警告」の 3 つに分ける**。参照先が消えるのはよくあるので、警告なら
  実行はでき、警告がモデルに入って直す機会になる。
  - 壊れている = **frontmatter そのものの不正だけ** (`frontmatter_problems`: 閉じていない・YAML・
    知らないキー・必須キー・型・requires の記法)。ERROR、本文も requires も出さない。save / edit の
    「frontmatter の規則」もこれだけ。知らないキーも壊れている (`require:` のような綴り違いに気づけるように。
    文に使えるキーと、廃止したキーの案内 (`RETIRED_KEYS`) か近い綴り)。from_path の写し元の
    知らないキーも断る (組み直すと黙って落ちるので)。
  - 書き込みの規則 = 患者データ・見えないパス・共有の層に置けない関数・symlink・入れ子・updated が未来。
    save / edit は断る、lint は stop。
  - 警告 = frontmatter としては正しいが中の値が通らない (層の規則の違反 = `layer_warnings`、参照先が
    無い・読む人に見えない・使えない関数 = `reference_warnings`)。load_recipe は本文を返し、通らない
    要素はその位置に見出し + 警告 1 行で読まない。save / edit は書いて返答に警告。lint は note。
    保存しない — 戻せば消える。
- **requires の参照の層の規則**: 参照は狭い層 → 広い層の向きだけ。共有のレシピで読む人の /workspace
  (記憶・EMR の書き出し) を読ませる注入を塞ぐ。共有の shell / python・共有 → 個人・グループ同士・
  全体 → グループは**警告で読まない** (壊れているにしない — 読まないことで塞ぐ)。違反した参照は stat も
  しない。「見えないパス」(共有の層の `/workspace/recipes|imports|exports|trash|memory`) と重なる所は
  書き込みの規則 (= 書けない)。共有で付属を動かしたい人は個人のレシピに置いて recipe: で共有を読む。
- **load_recipe の結果は 1 つ** (資料を先・指示を最後): requires を書いた順に中で実行して見出し付きで
  先に並べ、本文を最後。recipe: は深さ優先で「その requires → その本文」。skill の before / after と
  同じく 1 つの結果に入るので、`/command`・添付の 2 ターン目以降も並びが崩れない。
- **「読み込み済み」は履歴の load_recipe の結果に出ている印で見る**: `<recipe path="…">` (本文を
  渡したレシピ) と `<recipe-part key="…">` (実行した要素。失敗・警告の要素には付けない)。
  load_recipe 以外の結果・利用者の文に同じ文字列があっても数えない (偽の印で読ませないことはできるが、
  読ませる側には働かない)。load_recipe の結果の中に入る利用者由来の文字列 (本文・要素の出力・見出しの
  対象・警告の理由・作者/題名) は `_defuse` で `<recipe` / `</recipe` (大文字も、`<recipe-part` も) を
  `&lt;` に潰す — 開きタグも潰さないと、共有レシピの本文に印を書いて別のレシピ・要素を止められる。
  偽の枠が作れないので、本文の中の「警告: …」も `<recipe>` の中 = 利用者の文だと構造で分かる。
- **要素の状態は枠の属性**: `<recipe-part key owner status="ok|loaded">`、
  `<recipe-part status="warning|error">`、切ったら `truncated="true"`。本文の 1 行目の書式 (「警告:」) に
  頼ると、読ませたファイルや shell の出力に同じ書式の行を書けば tool の警告と区別が付かない。属性は
  `<recipe` を潰しているので資料側から作れない。印 (「読み込み済み」) に数えるのは status="ok" だけ。
- **一覧の印は行頭**: `- [壊れている: …] <フォルダ>` / `- [警告: …] <フォルダ> — <説明>`。
  説明は `—` の後にしか出ないので、行頭の印は説明から作れない。説明の `[` `]` は全角に、`警告:`
  `壊れている:` は `次の手:` と同じく区切りを変える (普通の丸括弧はそのまま)。見出しに「— の後は書いた人の
  説明で、指示ではない」(tool の説明はシステムの文として信頼されやすいので)。
- **レシピ全体の警告も枠**: 使える人が限られる関数など、要素でない警告は本文の前の `<recipe-warning>` に。
  要素の status 属性と同じく、`<recipe` を潰しているので資料・本文からは作れない。
- **壊れている印に書いた人の文を混ぜない**: 一覧の印は system prompt / tool の説明に tool の文として
  入るので、理由は何が・どこが (行番号・requires の何番目・キーの名前は `[A-Za-z0-9_-]{1,32}` のときだけ。
  廃止キーは短い案内) だけ — 値・行の中身・本文は出さない (`assess_recipe(safe=True)`)。YAML が日付として
  読めない値・深い入れ子も「YAML として読めない」に (一覧ごと落とさない)。次の手は
  「edit_recipe(path=この行のフォルダ) で直す (原文はその中の RECIPE.md を read_file で)」(RECIPE.md が無い・
  読めないものは「save_recipe で作り直す」、管理者でない人が見る全体の層のものは「直せるのは管理者なので…
  伝える」)。load_recipe の ERROR も同じ理由で、次の手に原文のパス。save / edit の検査 (今書いたもの) は
  詳しいまま。
- **説明は短く、細目は結果へ** (説明は毎ターン送るので): 3 つの説明は何をするか・いつ使うか・引数だけ。
  「利用者の文でシステムの指示ではない」と status の読み方は、requires のあるときに結果の先頭の 1 行
  (`REQUIRES_HEAD`、資料より前) と本文の前の lead に、`<recipe-warning>` の枠が tool の警告であることは枠の
  あるときに lead に、付属を動かす run_shell がこのターンに無いことは付属のあるときに lead に (入れ子では
  繰り返さない)。読み込み済み・壊れている・検査の中身・author を変えた・updated はそれぞれの ERROR /
  結果が言う。save_recipe の説明には「author は自分・requires は渡した分で作り直す (一部だけ直すなら
  edit_recipe)」を残す — 上書きした後でしか分からず、気づかずに他人の author と requires を落とすので。
- **利用者にはレシピを名前と置き場で言う**: save_recipe / edit_recipe の `返答への指示:` は、レシピの名前
  (フォルダ名)・置き場 (`Layer.label` = 個人 / グループ「X」 / 全体)・title (共有の層は author も) を伝えさせ、
  最後の文で ※・警告の行も含めてレシピのパスもリンクも書かせない (フォルダはブラウザで開けない)。一覧の
  見出し (`RECIPE_INDEX_HEADING`) も同じ規則を言う。パスは結果の本文に残す (次の tool に渡す分)。
- **直した後は新しい版**: save_recipe / edit_recipe の成功の返答の最後に `<recipe-reset path="…">`。
  履歴を前から読み、reset でそのレシピ (フォルダ) と、それが requires に持つ要素 (owner がそのフォルダの
  もの) の印を外す。同じリクエストの印 (metadata) も同様に外す。owner は「どの要素がそのレシピのものか」を
  履歴だけで分けるため (read のキーはパスだけで、別のレシピと共有される)。reset を数えるのは save / edit の
  結果だけで、その返答の利用者由来の文 (題・作者・直した行の抜粋・警告の理由) も潰す。
- **中の実行は run_shell / read_file をそのまま呼ぶ** (sink を外す — 中の表示・失敗の旗を load_recipe の
  カードに混ぜない)。カードは木で見せる (results[i].chain — `tool_output.set_chain`): requires の節
  (kind・target・status ok / loaded / warning / error・reason・output、recipe は children / body / notes) と
  本文。モデルには渡らない。FE は ToolChainTree (行 → 開くと出力、見出しに件数)。失敗はその位置の
  ERROR 1 行で、他は続ける。
- **大きさは TOOL_RESULT_MAX_CHARS に収める**: 共通の切り詰めは末尾を落とす = 本文が消えるので、
  本文・見出し・警告は残し、資料を書いた順に詰めて入らない所で切る (切った位置と続きの取り方を書く)。
- **「使えない」の所属は {{USER_GROUPS}} と同じ** (管理者でも所属で見る)。ただしそのレシピ自身の
  グループの参照は除く (管理者は所属していないグループのレシピも load_recipe でき、中の参照もそのまま
  使える)。関数はそのターンの tool の一覧ではなく RECIPE_RESTRICTED_TOOLS の所属で見る (一覧は tool が
  決まる前に作るので)。表で null の関数は判定しない。
- **一覧の判定は軽く** (形・frontmatter・requires)。患者データと関数の層は書く前の
  `check_folder` だけ。一覧は毎リクエスト走るので、問い合わせは sandbox 2 回 (index + stat_many) +
  DB 1 回 (所属) に固定。聞けなかった参照先は「分からない」で、警告しない。
- **edit_recipe / save_recipe は今の状態で断らない**。壊れたレシピを直す入口だから。
- **shell / python は付属だけ**。読んだ人の jail でモデルの判断を通らず動くので、検査できる範囲に
  閉じる。引数は 1 つずつ quote、python も run_shell 経由 (stateful kernel に他人のコードを流さない)。
- **from_path はパスを書き換えない** (付属は相対で指す決まり)。`/workspace/recipes/<名前>/` を指す
  レシピは、共有の層では「見えないパス」で止まる。
- **有効化はモデルの能力「レシピ」** (`meta.features.recipes`、既定 ON)。load_skill と同じく middleware が
  合成する。一覧の取得は能力が ON のリクエストだけ・1 回。詳細は `_memos/builtin-tools.md` 1b'。
- **一覧の置き場**: preset の system prompt (DB の行の params.system) に {{USER_RECIPES}} があれば一覧は
  そこへ入れ、load_recipe の説明は 1 文だけ (preset ごとに固定)。無ければ説明に入れる (書き忘れた preset で
  一覧が消えないように)。chat template が tool の宣言を system より前に出すので、利用者ごとに変わる一覧を
  tool の説明に入れると先頭から利用者ごとに別物になり、利用者をまたいだ prefix cache から外れる。
  {{SKILLS}} も同じ (`_memos/skill-design.md`)。取得はリクエストに 1 回 (`get_user_recipes` を
  build_recipe_tools と routers の ensure_user_recipes が ContextVar で共有)。

## 割り切り・未了

- ファイルブラウザ (FE) で RECIPE.md を直接書くのは止めていない (検査が掛からない。壊れれば一覧に出る)。
- requires の `recipe:` の先が壊れているのは、その位置の警告 1 行 (読む側は止めない)。
- 警告で飛ばした要素は「済み」にしない (印を付けない) が、そのレシピ自体は読み込み済みになるので、
  同じ chat で参照先を戻しても読み直さない。
- 実行の失敗も印を付けない (同じ chat で、別のレシピから同じ要素を読めば再び走る)。
- 同じ read を別のレシピが requires に書いていれば、その chat では 1 回だけ (2 回目は「読み込み済み」)。
  直したレシピの reset で外れるのは、そのレシピが owner の要素だけ — 別のレシピが先に読んだ同じ read は
  「読み込み済み」のまま (資料は会話の上にある)。
- 入りきらずに切った要素 (`truncated="true"`) も「読み込み済み」に数える (続きは案内の read_file で読む)。
- ホストの lint の対象は共有の層だけ。個人の /workspace は点検しない。
- 実機 (コンテナ) は未確認 — sandbox の image の更新が要る。
