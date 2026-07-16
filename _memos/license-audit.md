# ライセンス監査(issue #2)

実施日: 2026-07-05(develop ブランチ)。対象: 依存パッケージのコピーレフトスキャン。

## Python 依存(uv 環境、284 パッケージ)

強コピーレフト(GPL/AGPL/SSPL/NC)は 1 連鎖のみ検出:

- `extract-msg`(GPL)→ `RTFDE` → `oletools` → `pcodedmp`(GPLv3)
  - 用途: `.msg`(Outlook)ファイルのアップロード読込
    (`retrieval/loaders/main.py` の `OutlookMessageLoader`)のみ。
    backend コードから直接 import されておらず、langchain 経由の遅延 import。
  - **対応済み**: `extract_msg` を pyproject から除外(依存連鎖ごと消滅)。
    .msg アップロード時は langchain が「extract_msg 未インストール」エラーを
    返す(現行 llens 本番も RAG_ALLOWED_FILE_EXTENSIONS で .msg を許可していない)。
  - 復活させる場合はライセンス影響(GPL の頒布条件)を先に整理すること。

備考: pandas は BSD(ライセンス本文に "R" の文字が含まれるための誤検知)。

## フロントエンド依存(pnpm)

- GPL/AGPL/SSPL/NC 系: **なし**
- `LGPL-3.0-or-later`: `@img/sharp-libvips-linux-x64`(sharp の prebuilt libvips)のみ。
  弱コピーレフトで、改変せず動的リンク相当の利用のため頒布可。
  ビルド時依存(devDependencies 経由)であり配布バンドル(build/)には含まれない。
- `LIL`(@sveltejs/svelte-virtual-list)= MIT 系の冗談表記(Lil License、許諾的)。
- `Unknown`(khroma)= リポジトリにライセンス表記なし(mermaid の依存)。
  実質 MIT として広く利用されているが表記欠落。要ウォッチ。
- その他は MIT / Apache-2.0 / BSD / ISC / MPL-2.0 / CC0 等の許諾的ライセンス。

## NC 期残存コードの確認 — **完了(リスク実質ゼロ)**

LICENSE 変更履歴(ollama-webui 期):

| 日時(PST) | コミット | 内容 |
|---|---|---|
| 2023-11-14 13:15 | edef7b1a1 | MIT → CC BY-NC-SA 4.0 に変更 |
| 2023-11-14 13:21 | de227b588 | NC ライセンス本文の整形 |
| 2023-11-14 13:32 | 9c0576149 | **MIT に復帰(revert)** |

- CC BY-NC-SA だった期間は **同日 17 分間** のみ。
- この間の非マージコミットは LICENSE 変更 2 件(いずれも Timothy J. Baek 本人)だけで、
  コード差分は README 1 行のみ(`git diff edef7b1a1 9c0576149 -- . ':!LICENSE'`)。
- 第三者のコード contribution は皆無であり、著作権者本人が即日 MIT へ戻しているため、
  **NC 期由来コードの残存リスクは実質ゼロ**と結論する。
- その後 v0.6.5 まで: MIT → 2025-01-10(a76068d69)で BSD-3-Clause 系へ。
  v0.6.5 は無制約 BSD-3-Clause(fork 基点として問題なし。upstream-policy.md 参照)。

## 未了(後続)

- [x] 依存スキャンの CI 化(GPL/AGPL/SSPL/NC を検出したら fail するチェック)— f7f27943d で `test-backend.yaml` に追加済み

## 再実行手順

```bash
# Python
uv run python - <<'EOF'
import importlib.metadata as md, re
for d in md.distributions():
    lic = (d.metadata.get('License') or '') + ' ' + ' '.join(
        c for c in (d.metadata.get_all('Classifier') or []) if 'License' in c)
    if re.search(r'GPL|AGPL|SSPL|NonCommercial|CC-BY-NC|EUPL', lic, re.I) \
            and not re.search(r'LGPL', lic):
        print(d.metadata['Name'], '|', lic.strip()[:120])
EOF

# Frontend
pnpm licenses list
```

## 追記(2026-07-17 棚卸し)

- **.msg(Outlook)の扱いは前提ごと消滅**: 上記の langchain ローダ経路は 2026-07-14 の
  文書抽出 markitdown 一本化(#52)で langchain-community ごと撤去済み。extract_msg の
  GPL 連鎖を「入れない」判断自体は不変(markitdown の extras にも含めない)。
- **LICENSE 再構成(2026-07-16、#2 #58)**: 本体 `LICENSE` = Technoplasm, Inc. 専有、
  upstream BSD-3-Clause 全文は `LICENSE.open-webui` へ退避(表示保持義務を充足)。
  About タブに fork 帰属表記 + 依存ライセンス一覧(static/licenses.json)を表示。
