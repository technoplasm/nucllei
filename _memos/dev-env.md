# 開発環境メモ

## Node.js / pnpm

- 基準バージョン: `.node-version`(node 24.18.0)/ `packageManager`(pnpm 11.3.0)。
  pnpm 11 は node >=22.13 が必要(`engines` に反映済み)。
- **node v24.4.0 は使用禁止**: pnpm install のリンク/hoist フェーズで V8 が
  `FATAL ERROR: invalid array length Allocation failed - JavaScript heap out of memory`
  でクラッシュする(pnpm 10.13.1 / 11.3.0 の両方で再現、node 24.18.0 / 26.4.0 では再現せず)。
  V8 側の不具合であり、node 24.18.0 以降へ更新すれば解消する。
- lifecycle script の許可リストは `pnpm-workspace.yaml` の `allowBuilds`。
  pnpm 11 は未記載のパッケージの build script を「未レビュー」としてエラーにするため、
  拒否するものも明示的に `false` を書く。
  - `sharp: false` — @img/sharp-linux-x64 の prebuilt を使用。install script は
    prebuilt 検出失敗時に node-gyp + libvips のソースビルドへフォールバックして失敗する。
  - `onnxruntime-node: false` — CPU 用バイナリは npm パッケージ同梱。postinstall は
    GPU(CUDA)用 .so の外部ダウンロードで、air-gapped 制約(fork.md §6)に不適。
  - `core-js: false` — postinstall は寄付案内の表示のみ。

## Python

- `uv sync` で backend 依存を導入(重量 ML 依存は `local-ml` extra に分離済み)。
- バックエンドテスト: `backend/open_webui/test/` 配下、pytest。

## Pyodide(削除済み)

- Pyodide 実行系は 2026-07-05 に全面撤去(issue #11)。`pnpm dev` / `pnpm build` から
  `pyodide:fetch`(62MB ダウンロード)が消え、npm 依存・worker・static/pyodide も無い。
- コード実行は外部 nsjail + IPython(自前 Tool 側)。管理画面のエンジンは jupyter のみ。

## テストの実行

- backend: リポジトリルートで `uv run pytest`(postgres コンテナは conftest が
  docker で自動起動・セッション共有・終了時削除。docker 必須)。
- モデル出力フレームの実キャプチャ fixture: `_memos/model-frames.md` 参照。
  再キャプチャは `uv run python scripts/capture_model_frames.py`(要 SGLang 接続)。
- fixture(`backend/open_webui/test/fixtures/`)は prettier 対象外(バイト列固定)。

## svelte-check の状態(2026-07-05)

- `pnpm check`(svelte-check)は v0.6.5 ベースラインで **5056 errors / 67 warnings / 226 files**。
  upstream も green にしていない領域のため CI には入れていない(lint 再有効化は issue #25 の残タスク)。
- `pnpm build`(vite build)と `pnpm test:frontend`(vitest)は green。CI 必須化済み。

## 起動時の static 上書き(2026-07-05)

- `config.py` は import のたびに `FRONTEND_BUILD_DIR/static/**`(既定 `build/static`)を
  `backend/open_webui/static/` へ**無条件上書きコピー**する。古い `build/` が残っている
  マシンでは、uvicorn --reload の再起動のたびに favicon 等が旧アセットへ巻き戻る。
  ブランド画像を差し替えたら `static/static/**`(ソース)と、ローカルの `build/static/**`
  も同期すること(favicon 差し替え時に一度これで巻き戻りが発生 → amend で修復済み)。
