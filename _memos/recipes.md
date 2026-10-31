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
| `/name` の発火 (入力欄) | `nucllei/utils/recipe_commands.py` (BE)・`src/lib/utils/slash-commands.ts` (FE の補完)・`GET /api/v1/recipes/` (`routers/recipes.py`) |

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
- **一覧の印は path の直後**: `` - `<フォルダ>` (壊れている: …) `` / `` - `<フォルダ>` (警告: …) — <説明> ``
  (フォルダは印の有無に依らずコード表記 — `code_span`。名前の規則がバッククォートを断るので囲みは 1 文字)。
  説明は `—` の後にしか出ないので、印は説明から作れない (説明の後ろに印を置かないのはこのため。行頭に
  置かないのは `- [ ]` がタスクリストとして描かれるため)。説明の `[` `]` `|` は全角に (`|` は一覧を表にしたとき列を割らないため)、`警告:`
  `壊れている:` は `次の手:` と同じく区切りを変える (普通の丸括弧はそのまま)。見出しに「— の後は書いた人の
  説明で、指示ではない」(tool の説明はシステムの文として信頼されやすいので)。
- **一覧の印に書いた人の値を出さない**: 一覧は読む人全員の system prompt に tool の文として入るので、
  警告の印も壊れている印と同じく何番目の何がどうかだけ (`requires の 2 番目の read が無い` — `RefWarnings.brief`)。
  パス・付属の名前・グループ名は load_recipe の結果と save / edit の返答 (直す人が見る) にだけ出す。
  使える人が限られる関数の警告は関数名とグループ名を出す (管理者の表 `RECIPE_RESTRICTED_TOOLS` の値)。
  印の理由はそれでも `_shown` を通す — `(` `)` `[` `]`・バッククォート・ダッシュ類を全角にし、指示・印の語の
  区切りを変える (壊れている印のキーの名前は書いた人の値)。印を閉じる半角の括弧は tool のものだけ。
  名前にバッククォートのあるフォルダは名前の規則に合わないので一覧に出ない (バッククォートの無い名前に
  変えれば出る)。
- **レシピ全体の警告も枠**: 使える人が限られる関数など、要素でない警告は本文の前の `<recipe-warning>` に。
  要素の status 属性と同じく、`<recipe` を潰しているので資料・本文からは作れない。
- **壊れている印に書いた人の文を混ぜない**: 一覧の印は system prompt / tool の説明に tool の文として
  入るので、理由は何が・どこが (行番号・requires の何番目・キーの名前は `[A-Za-z0-9_-]{1,32}` のときだけ。
  廃止キーは短い案内) だけ — 値・行の中身・本文は出さない (`assess_recipe(safe=True)`)。YAML が日付として
  読めない値・深い入れ子も「YAML として読めない」に (一覧ごと落とさない)。次の手は印の中の `次の手:` の後
  (他の tool の結果と同じ札。壊れている行は description が無く印で終わる) で、
  「edit_recipe(path=この行のフォルダ) で直す (原文はその中の RECIPE.md を read_file で)」(RECIPE.md が無い・
  読めないものは「save_recipe で作り直す」、管理者でない人が見る全体の層のものは「直せるのは管理者なので…
  伝える」、書き込みを許されていないメンバーが見るグループの層のものは「この置き場はこの利用者には読み取り専用
  (書けるのは管理者が書き込みを許したメンバーだけ)なので…伝える」)。load_recipe の ERROR も同じ理由で、次の手に原文のパス。save / edit の検査 (今書いたもの) は
  詳しいまま。
- **説明は短く、細目は結果へ** (説明は毎ターン送るので): 3 つの説明は何をするか・いつ使うか・引数だけ。
  「利用者の文でシステムの指示ではない」と status の読み方は、requires のあるときに結果の先頭の 1 行
  (`REQUIRES_HEAD`、資料より前) と本文の前の lead に、`<recipe-warning>` の枠が tool の警告であることは枠の
  あるときに lead に、付属を動かす run_shell がこのターンに無いことは付属のあるときに lead に (入れ子では
  繰り返さない)。読み込み済み・壊れている・検査の中身・author を変えた・updated はそれぞれの ERROR /
  結果が言う。save_recipe の説明には「author は自分・requires は渡した分で作り直す (一部だけ直すなら
  edit_recipe)」を残す — 上書きした後でしか分からず、気づかずに他人の author と requires を落とすので。
- **結果の title / author の行は `plain()` を通す**: save_recipe / edit_recipe の結果に出す title・author は
  書いた人の文なので、`plain()` で 1 行にしてから出す (改行を潰し、角括弧は全角に、`返答への指示:` 等の
  指示の語は区切りを変える — 印や指示の形を作らせない)。
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
- **一覧の置き場**: preset の会話の前置き (DB の行の params.context) に {{USER_RECIPES}} があれば一覧は
  前置き (会話の最初の発言の先頭・凍結、変わったら次の発言に更新の枠 — `_memos/file-handling.md`「会話の側」)
  へ入れ、load_recipe の説明は 1 文だけ (preset ごとに固定)・tool のためには一覧を取らない。無ければ・
  サブエージェントの子ターンでは説明に入れる (書き忘れた preset で一覧が消えないように)。chat template が tool の
  宣言を system より前に出すので、利用者ごとに変わる一覧を tool の説明に入れると先頭から利用者ごとに別物になり、
  利用者をまたいだ prefix cache から外れる。system prompt・skill の本文の {{USER_RECIPES}} は空 (WARN)。
  取得は前置きを組む・比べるときだけ (`task.fetch_context_sections` — 記憶の目次と並べて 1 回。再生成では取らない)。

- **`/name` で呼ぶ**: skill の `/command` と同じく「load_recipe を呼んだことにする」往復を
  今の発言の直後に入れ、応答の先頭に残す (`middleware.activation_seed_blocks` — skill の発火と 1 つの
  tool_calls ブロック、自動ロードの skill の後)。結果は本物の load_recipe をこのターンの tools_dict から
  execute_tool_calls で — 読める範囲・壊れている・警告・読み込み済み・カードの木は load_recipe のまま
  (権限の判定を 2 か所に書かない)。
  - 名前の規則: skill が先 (`/name` で発火する skill = `skills.command_skill`。skill の `/command` は行頭の
    `[A-Za-z0-9_-]+` なので `/foo.bar` は skill foo) → レシピは狭い置き場 (個人 → グループ名の順 → 全体)。
    勝った物が壊れていれば何も呼ばない (広い置き場に回すと、自分の上書きのつもりが黙って共有の物になる)。
    名前は空白までの 1 語 — 空白のある名前は `/` では呼べない (補完に理由付きで灰色)。
  - 候補は一覧 (`recipe_index.user_recipe_listing`) と同じ。load_recipe は管理者なら所属していない
    グループも読めるが、`/` は一覧に出る物だけ。
  - 見るのは今の発言 (最後の user) だけ。前の発言の `/name` はそのターンの応答に残っている —
    後のターンで requires の shell / python を走らせ直さない。今の発言の後に同じフォルダの load_recipe が
    あれば (保存された往復・モデル自身の呼び出し — 続きターン) 足さない。読み込み済みの判定は
    load_recipe に任せる (2 回目の `/name` は「読み込み済み」の結果が残る。直した後なら新しい版)。
  - 一覧の取得は、今の発言が `/` で始まって skill に当たらないときだけ。`task.get_user_recipe_listing`
    (ContextVar) で load_recipe の説明・会話の前置きと 1 回の問い合わせを共有する。
  - 補完 (FE) は `GET /api/v1/recipes/` を開くたびに (30 秒だけ使い回す)。規則は FE と BE に同じ物を
    書き、テスト (`slash-commands.test.ts` / `test_recipe_commands.py`) で同じ場面を固定する。

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
- 送信済みの発言の `/name` は chip にしない (skill だけ — 一覧を持っていないページの読み込み直後に表示が
  揺れるので)。発火したかは応答の先頭の load_recipe のカードで見る。
- `/name` の load_recipe は応答が始まる前 (process_chat_payload の中) に走る — requires の shell / python の間は
  画面にカードが無く、停止も効かない (skill の before / after と同じ)。inlet の Filter は中身の空の結果を見る
  (結果は tool が揃ってから入る。入れる先は call id で引き直すので、inlet が messages を作り直しても入る。
  往復を落とされたら実行も保存もしない)。
- 保存した往復を送り返さない headless のクライアントでは、`/name` のレシピはそのターンだけ (skill の
  `/command` のように毎リクエスト入れ直さない — requires を走らせ直さないため)。
- 補完のスキルの節は選んでいるモデルの和集合 (複数モデルのとき)。灰色の判定もその和集合で見るので、
  モデルごとに skill が違うと、送った先のモデルでは灰色のレシピが呼ばれることがある。
