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
