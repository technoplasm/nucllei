# sandbox — 実装状況 / 残課題

作業管理の台帳 (進捗で陳腐化する情報)。設計・API・セキュリティの単一情報源は [README.md](README.md)。

残課題のうち **配備 (LLENS オンプレ) 側の運用に属するもの** — version pin の実施・workspace の
GC policy 運用・監視配線 — は、この repo で image を作れることとは別に配備層
(`prism-hu/llens`) が持つ。ここには「image 側で何が要るか」だけを残す。

## 実装・検証済み

- [x] nsjail 隔離 (netns 空 = SSRF 遮断 / rootfs ro / nobody / rlimit / root tmpfs 16MB)
- [x] per-user disk quota (sparse loopback ext4, thin + discard + hard cap)。privileged 採用
- [x] stateful `run_python` (会話ごと常駐 kernel, 永続 namespace, SIGALRM でセル中断,
      idle 回収 + session 上限)。`run_python_once` / `run_shell` は stateless
- [x] ファイル入出力 (send_to_workspace / exports 自動添付 / fetch_from_workspace)、画像 inline・
      文書リンクの使い分け指示
- [x] パッケージ構成確定 (requirements.txt)、BLAS スレッド固定、日本語フォント既定化
- [x] 利用指針を system prompt に集約、docstring 縮小
- [x] workspace 衛生 (cache を /tmp へ、list_workspace の dotfile 非表示)
- [x] Pyodide コード実行の撤去 (nucllei では実行系ごと削除済み — #11 / #23)
- [x] 隔離の回帰テスト (`sandbox/tests/test_isolation.py`, `uv run task sandbox-test`)。
      jail の中から権限昇格 (nobody / setuid 拒否 / rootfs ro) とデバイス・システムの
      見え方 (dev 5 ノードのみ / ホスト block・GPU 不可視 / 無ネットワーク / df=quota /
      PID ns) を実際に突く。稼働中の privileged コンテナ必須 (無ければ skip)
- [x] `write_file` tool (モデルが書いたテキストを直接 workspace へ)。裸の HTML/CSV を
      run_python の文字列や run_shell のヒアドキュメントに埋めるクォート事故を構造的に潰す
- [x] HTML/CSS → PDF (weasyprint + libpango)。`pandoc --pdf-engine=weasyprint` で md → PDF も。
      Chromium は見送り (理由は README「Python 環境 / シェルツール」)
- [x] 健全性維持 (閉域・無再起動前提)。設計は [docs/health.md](docs/health.md)。
      ① idle-detach reaper (idle workspace を umount して loop 単調増加を根治) /
      ② GET /metrics (loop/workspace/session/disk。手書き・依存なし) /
      ④ DELETE /workspace mechanism (外部 GC 用、trash rename) /
      C9 ext4 brick 耐性 (破損 .img を退避 → fresh、atomic 生成) / C5 枯渇 503。
      exec/exec_cell 回帰なし・live_session ガード・C9 復旧を実機検証済
- [x] **API の共有トークン認証** (#132、2026-09-04)。`Authorization: Bearer <SANDBOX_TOKEN>`
      を middleware で全パスに要求 (免除は `/healthz` `/metrics` のみ)。**未設定なら起動
      拒否**の fail-closed、比較は `hmac.compare_digest`。呼び出し元 (nucllei) 側は
      `SANDBOX_TOKEN` 未設定だと `builtin_sandbox` を登録しない。回帰は
      `sandbox/tests/test_auth.py` + `nucllei/test/.../test_builtin_sandbox.py`。
      設計は README「セキュリティモデル」。**配備側 (llens) の追従は向こう持ち** =
      compose の 2 サービス (sandbox / nucllei) に同じ `SANDBOX_TOKEN` を渡す + 外部 GC
      スクリプトにヘッダを足す。Prometheus と healthcheck は免除なので変更不要

## 残課題

- [ ] **実チャットでの実践シナリオ検証** (プロット/画像/Office 読み書き/複数セル連携)
- [ ] requirements.txt / apt ツールの version pin (閉域搬入前に必須。構成は確定済)
- [ ] 古いユーザーの workspace イメージ掃除 — 内部 mechanism (DELETE /workspace) は実装済 =
      **image 側は完了**。残る外部 policy script (nucllei-db の非 active user を照合 →
      dry-run → DELETE 呼び。時刻ベース削除は誤削除リスクで不採用) は**配備層 (llens) が持つ**
      — DB を引くので image に DB 資格情報を持たせない。docs/health.md ④ 参照
- [ ] 監視配線 (prometheus job + Grafana alert: loop 0.75 / disk 0.80) は**配備層 (llens) 側**。
      image 側の `/metrics` は実装済
- [ ] per-user uid 分離 (懸念として保留。README のセキュリティモデル参照)
- [ ] seccomp 絞り込み (現状 nsjail デフォルト。コスト重く優先度低・見送り可)
