---
name: 大容量ファイル処理
description: '巨大な添付ファイルを、中身を主コンテキストに展開せずに処理するワークフロー。『large-file-gate』のシステム指示が添付の本文を context に入れていない (または docling 抽出が空) と file_id 付きで告知した時、または巨大な CSV/Excel/PDF/ログ等を全文を会話に出さずに集計・要約・抽出したい時に使う。該当したら作業を始める前に必ず load_skill で本 skill の全文をロードし、その手順に従うこと。'
tool_ids: ['sandbox', 'subagent']
---

# 大容量ファイル処理 (中身を context に出さずに扱う)

## いつ使うか

- **large-file-gate** filter が次を告知した時。そのファイルの中身は**プロンプトに無い**ので、file_id から取り込んで処理する:
  - 「大きすぎるため本文を context に入れていない (file_id=…)」
  - 「docling 抽出が空だった (file_id=…)」(大きすぎて抽出がタイムアウトした等)
- 巨大な CSV / Excel / PDF / ログ等を、**全文を会話に展開せずに**集計・要約・抽出したい時。

## 大原則

1. **中身を print しない。** stdout は会話 context に入る。出してよいのは「サイズ・shape・行数・列名・ページ数などのメタ情報」と「file_id」だけ。個票・本文は出さない (院内 = 個人情報前提)。
2. **前処理 (削る・割る・集計・変換) は `run_python` でやる。** サーバー実行なので速く、pandas / pypdf / openpyxl / python-docx 等がそのまま使える。会話内で変数・import は永続 (notebook のセル)。
3. **中身の「理解・要約」が要る時だけ `ask_subagent` に投げる。** サブエージェントは別 context・別推論で読み、資料の中身は主 context に出ない。返るのは短い answer だけ。**サブエージェントは tool 不可 / 一度に約 100k tokens まで**なので、飲める形に削る/割る前処理はこちらでやってから渡す。

## 取り込み (jail はネットワーク不可 → 必ず import_chat_file 経由)

`run_python` の jail からは `/api/v1/...` を直接叩けない。ファイルは `import_chat_file` で `/workspace/inputs/` に取り込んでから読む。

- **gate で外れた添付** → note の `file_id` で取る: `import_chat_file(file_id="…")`
- **通常の添付** → 名前で取る: `import_chat_file(filename="report.csv")` (部分一致)

取り込んだら run_python で `/workspace/inputs/<name>` を読む。

## run_python での処理

サーバー実行なので pandas / pypdf / openpyxl 等をそのまま普通に使ってよい。まず**メタだけ**確認して戦略を決める:

```python
import pandas as pd
df = pd.read_csv("/workspace/inputs/report.csv", encoding="cp932")  # 電子カルテ系は cp932 が多い
print(df.shape, list(df.columns))   # 形と列名だけ (値は出さない)
```

- **エンコーディング**は決め打ちしない: `utf-8-sig` → 駄目なら `cp932` → それでも駄目なら `chardet`。
- PDF/Word は `pypdf` / `python-docx` でテキスト抽出、Excel は `openpyxl` / `pandas.read_excel`。docling md は jail からは取れないので、原本を run_python で読む。
- 状態は会話内で永続。前セルの `df` を次セルでそのまま使える。12h 放置でリセット (戻り値に注記が出たら定義し直す)。

## パターン

### A. 計算だけで済む (集計 / 絞り込み / 変換 / ルール抽出) — 最も多い

subagent は要らない。run_python で処理し、**小さい結果だけ** print する。

```python
n = (df["科"] == "呼吸器内科").sum()
print("呼吸器内科の行数:", int(n))      # 出すのは集計値だけ
df[df["科"] == "呼吸器内科"].to_csv("/workspace/outputs/subset.csv", index=False)
# outputs/ に置くと自動でチャットに添付され、ユーザーがダウンロードできる
```

### B. 中身の理解・要約が要る

- **subagent に収まる (~100k tokens 以下)** → 元の file_id をそのまま渡す。分割不要 (subagent が backend 側で読む):
  `ask_subagent(file_id="<元の file_id>", instruction="この文書の…を要約")`
- **絞れる** → run_python で該当部分だけ抜き、`export_workspace_file` で file_id 化して 1 回だけ渡す:
  ```python
  # 必要部分だけ書き出す (中身は print しない)
  hits = [ln for ln in text.splitlines() if "呼吸器" in ln]
  open("/workspace/extract.txt", "w").write("\n".join(hits))
  print("抽出行数:", len(hits))
  ```
  → `export_workspace_file("extract.txt")` が返す `file_id=…` を `ask_subagent(file_id=…, instruction=…)` に渡す。
- **全網羅で分割が要る (稀)** → run_python で N 分割 → 各片を `export_workspace_file` → 各 file_id を `ask_subagent` → 返った answer をこちらで統合。断片ごとに tool 呼び出しが増えるので、本当に全網羅が要るか先に検討する (多くは A か「絞れる」で足りる)。

## file_id 化 (export_workspace_file) の注意

`export_workspace_file(path)` は workspace のファイルを OWUI Files に登録し `file_id` を返すが、**チャット添付にもなる**ので次ターンに再注入される (大きいものは large-file-gate が再度弾く)。中間成果物は極力 **1 個に絞る** (絞り込み → 1 export → 1 subagent)。ユーザーに渡す最終成果物は `/workspace/outputs/` に置けば自動添付される。

## ask_subagent への渡し方

- `ask_subagent(file_id=…, instruction=…)` … 新規。資料を読ませて結論だけ返す。`thread_id` も返る。
- `ask_subagent(thread_id=…, instruction=…)` … 継続。同じ資料・文脈のまま追い質問 (file_id 不要)。
- 資料の中身も会話履歴もサブエージェント側に隔離され、主 context には出ない。

## やってはいけないこと

- 中身 (本文・個票・全 md・DataFrame 全体) を print / 応答本文に貼る。
- 巨大ファイルの file_id を**そのまま** `ask_subagent` に渡す → 頭から約 100k tokens で truncate され尻尾が落ちる。必ず先に run_python で削る / 割る。
- 取り込みや file_id 化を挟まず、jail から直接ダウンロードしようとする (ネットワーク不可)。
