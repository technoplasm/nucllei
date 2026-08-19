---
description: '巨大な添付ファイルを、中身を主コンテキストに展開せずに処理するワークフロー。『large-file-gate』のシステム指示が添付の本文を context に入れていない (または抽出が空) と file_id 付きで告知した時、または巨大な CSV/Excel/PDF/ログ等を全文を会話に出さずに集計・要約・抽出したい時に使う。サブエージェント (run_subagent) への委譲とバックグラウンド実行 (background / await_task) の使い分けも本 skill が持つ。該当したら作業を始める前に本 skill をロードし、その手順に従うこと。'
---

# 大容量ファイル処理 (中身を context に出さずに扱う)

## いつ使うか

- **large-file-gate** filter が次を告知した時。そのファイルの中身は**プロンプトに無い**ので、file_id から取り込んで処理する:
  - 「大きすぎるため本文を context に入れていない (file_id=…)」
  - 「抽出結果が空だった (file_id=…)」(大きすぎて抽出がタイムアウトした等)
- 巨大な CSV / Excel / PDF / ログ等を、**全文を会話に展開せずに**集計・要約・抽出したい時。

## 大原則

1. **中身を print しない。** stdout は会話 context に入る。出してよいのは「サイズ・shape・行数・列名・ページ数などのメタ情報」と「file_id」だけ。個票・本文は出さない (院内 = 個人情報前提)。
2. **`file_id` を直接受け取る tool があるなら、それに渡して終わり。** 取り込みも run_python も要らない (下表)。
3. **前処理 (削る・割る・集計・変換) は `run_python` でやる。** サーバー実行なので速く、pandas / pypdf / openpyxl / python-docx 等がそのまま使える。会話内で変数・import は永続 (notebook のセル)。
4. **中身の「理解・要約」が要る時だけ `run_subagent` に投げる。** instruction に file_id と指示を書けば、サブエージェントが別 context (agent session) で `read_attachment` により読み、資料の中身は主 context に出ない。返るのは短い answer だけ。
5. **時間がかかりそうな呼び出しは `background: true` で非同期にする。** 対応 tool (`run_subagent` / `run_python` / `transcribe_speech` 等) は `task_id` を即返して裏で走り、完了結果はサーバが自動で届ける。状態確認は `check_task`、今すぐ回収は `await_task(task_ids=[…])`、中止は `stop_task`。

## 渡し先を先に決める

`file_id` をそのまま受け取る tool は、添付を**取り込まずに**処理できる:

| やりたいこと                        | 渡し先                                                                                          |
| ----------------------------------- | ----------------------------------------------------------------------------------------------- |
| 画像・スキャン PDF を自分の目で見る | `feed_image(file_id=…)` — PDF は `pages="30"` でページ指定                                      |
| 中身を読ませて結論だけ得る          | `run_subagent` — instruction に file_id と指示を書く                                            |
| 音声を文字起こしする                | `transcribe_speech(file_id=…)` — 長い録音は `start_sec=…` で続きを取る (長さは `inspect_audio`) |

## 取り込み (run_python で自分で開く時だけ)

`run_python` の jail からは `/api/v1/...` を直接叩けない。**jail 内でファイルを開く必要がある時に限り**、`send_to_workspace` で `/workspace/imports/` に取り込んでから読む。

- **gate で外れた添付** → note の `file_id` で取る: `send_to_workspace(file_id="…")`
- **通常の添付** → 名前で取る: `send_to_workspace(filename="report.csv")` (部分一致)

取り込んだら run_python で `/workspace/imports/<name>` を読む。

## run_python での処理

サーバー実行なので pandas / pypdf / openpyxl 等をそのまま普通に使ってよい。まず**メタだけ**確認して戦略を決める:

```python
import pandas as pd
df = pd.read_csv("/workspace/imports/report.csv", encoding="cp932")  # 電子カルテ系は cp932 が多い
print(df.shape, list(df.columns))   # 形と列名だけ (値は出さない)
```

- **エンコーディング**は決め打ちしない: `utf-8-sig` → 駄目なら `cp932` → それでも駄目なら `chardet`。
- PDF/Word は `pypdf` / `python-docx` でテキスト抽出、Excel は `openpyxl` / `pandas.read_excel`。添付の抽出テキストは jail からは取れないので、原本を run_python で読む。
- 状態は会話内で永続。前セルの `df` を次セルでそのまま使える。12h 放置でリセット (戻り値に注記が出たら定義し直す)。

## パターン

### A. 計算だけで済む (集計 / 絞り込み / 変換 / ルール抽出) — 最も多い

subagent は要らない。run_python で処理し、**小さい結果だけ** print する。

```python
n = (df["科"] == "呼吸器内科").sum()
print("呼吸器内科の行数:", int(n))      # 出すのは集計値だけ
df[df["科"] == "呼吸器内科"].to_csv("/workspace/exports/subset.csv", index=False)
# exports/ に置くと自動でチャットに添付され、ユーザーがダウンロードできる
```

### B. 中身の理解・要約が要る

- **そのまま読ませられる** → 元の file_id を instruction に書いて渡す。分割不要 (サブエージェントが `read_attachment` で読み、長い文書は自分で続きを取る):
  `run_subagent(preset_id=…, instruction="file_id=<元の file_id> の文書を read_attachment で読み、…を要約して")`
- **絞れる** → run_python で該当部分だけ抜き、`fetch_from_workspace` で file_id 化して 1 回だけ渡す:
  ```python
  # 必要部分だけ書き出す (中身は print しない)
  hits = [ln for ln in text.splitlines() if "呼吸器" in ln]
  open("/workspace/extract.txt", "w").write("\n".join(hits))
  print("抽出行数:", len(hits))
  ```
  → `fetch_from_workspace("extract.txt")` が返す `file_id=…` を instruction に書いて `run_subagent` に渡す。
- **全網羅で分割が要る (稀)** → run_python で N 分割 → 各片を `fetch_from_workspace` → 各 file_id を `run_subagent(…, background=true)` で並行に投げ、`await_task(task_ids=[…])` でまとめて回収して統合。**並行は 4 本まで (超過は待たずに即エラー)** — 5 片以上は 4 本ずつのバッチで回す。断片ごとに tool 呼び出しが増えるので、本当に全網羅が要るか先に検討する (多くは A か「絞れる」で足りる)。

## file_id 化 (fetch_from_workspace) の注意

`fetch_from_workspace(path)` は workspace のファイルを Files に登録し `file_id` を返すが、**チャット添付にもなる**ので次ターンに再注入される (大きいものは large-file-gate が再度弾く)。中間成果物は極力 **1 個に絞る** (絞り込み → 1 export → 1 subagent)。ユーザーに渡す最終成果物は `/workspace/exports/` に置けば自動添付される。

## run_subagent への渡し方 (資料読み)

- 新規: `run_subagent(preset_id=…, instruction="file_id=… を read_attachment で読み、…")` — 資料を読ませて結論だけ受け取る。`session_id` も返る。
- 継続: `run_subagent(session_id=…, instruction=…)` — 同じ資料・文脈のまま追い質問 (file_id 再指定は不要)。
- 資料の中身も会話履歴もサブエージェント (agent session) 側に隔離され、主 context には出ない。
- file_id は **実在の literal をそのままコピーして書く** (組み立てない・改変しない)。
- 時間がかかりそうなら `background: true` を付ける (`task_id` が即返り、結果はサーバが自動で届ける)。

## やってはいけないこと

- 中身 (本文・個票・全抽出テキスト・DataFrame 全体) を print / 応答本文に貼る。
- `file_id` を直接受け取る tool で足りるのに `send_to_workspace` で取り込む。jail の disk を無駄に食う。
- 巨大ファイルを丸ごと 1 回の要約で片付けようとする → サブエージェントの context にも限界はある。数十万字級は先に run_python で削る / 割るか、instruction で読む範囲を指示する。
- 取り込みや file_id 化を挟まず、jail から直接ダウンロードしようとする (ネットワーク不可)。
