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
- `LGPL-3.0-or-later` が 1 系統あり(弱コピーレフト。改変せずライブラリとして
  使う限り頒布可)。要追跡: `pnpm licenses list` で特定可能。
- その他は MIT / Apache-2.0 / BSD / ISC / MPL-2.0 / CC0 等の許諾的ライセンス。

## 未了(後続)

- [ ] **NC 期残存コードの確認**: open-webui の前身(ollama-webui 期)や一時的な
  ライセンス変更期に由来するコードが v0.6.5 に残っていないかの確認。
  upstream の LICENSE 変更履歴(`git log --follow LICENSE`)を洗い、
  該当期間のコミットが現存ファイルに含まれるか blame ベースで確認する。
  ※ v0.6.6+ のコード閲覧禁止(upstream-policy.md)に抵触しない範囲
  (v0.6.5 以前の履歴)で実施可。
- [ ] 依存スキャンの CI 化(GPL/AGPL/SSPL/NC を検出したら fail するチェック)

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
