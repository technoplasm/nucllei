# llens — Open WebUI fork(基点 v0.6.5 固定)

北大病院オンプレ向け「薄い LLM チャット基盤」。llens 独自の文書はすべて `_memos/` にある:
機能要件の一次根拠は `_memos/fork.md`(CORE / KEEP-MINIMAL / DROP / CONSTRAINT の 4 区分)、
機能別の維持/削除判断は `_memos/feature-triage.md`、upstream の扱いは `_memos/upstream-policy.md`。
実装判断で迷ったらまず fork.md に照らす。

## 絶対制約

- **upstream v0.6.6 以降のソースコードを読まない・写さない**(タグ間 diff の実装内容の閲覧も含む)。
  参照してよいのはリリースノート・公式ドキュメント・API 外形仕様のみ。詳細は `_memos/upstream-policy.md`。
- upstream の無条件マージ禁止。取り込みはファイル/コミット単位、`_memos/upstream-policy.md` に由来を記録。
- コード実行は nsjail + IPython 外部サンドボックス(Jupyter 互換接続)。Pyodide には戻さない
  (Pyodide 実行系はコード・依存・ビルドステップごと削除済み — issue #11)。
- air-gapped 前提。外部ネットワーク必須の機能を増やさない。

## ワークフロー

- ブランチモデル: **`develop`** が開発 head(日常の作業はここに積む)、**`master`** は出荷ブランチ(develop から安定点をマージ)、
  **`main`** は fork 基点(upstream v0.6.5 分岐点)の凍結ブランチ — コミットを積まない。v0.6.5 タグはローカルに無いので基点参照は `origin/main` を使う。
- ロードマップは GitHub issue #16(tracking)。作業は issue 単位で進め、コミットメッセージに `refs #N` を入れる。
- セッション開始時は `/status` で現在地(ロードマップ進捗・v0.6.5 からの差分・テスト状態)を確認する。
- CORE 領域(Filter パイプライン / group 権限 / OpenAI 互換接続 / 認証)に触れる変更は回帰テストを伴うこと。

## 現行プロジェクトの参照

- `_links/llens` → prism-hu/llens(北大で稼働中の現行 OWUI ベース構成。マシン固有 symlink のため gitignore 済み)。
  現行の Filter 実装・nsjail sandbox(stateful run_python)・TTS 構成・SGLang 設定は適宜ここを参照する。
  リンクが無い環境では `~/src/github.com/prism-hu/llens` を確認。

## 環境の前提

- 推論エンジンは **SGLang**(`:8000`、OpenAI 互換)。接続モデルは Kimi K2.6 で reasoning 出力あり(`reasoning_content` / `<think>` タグ)。
- モデル接続は OpenAI 互換一本(`base_url` 設定だけで動く状態を最小構成として保証)。
- バックエンドテストは `backend/open_webui/test/` 配下(pytest)。
