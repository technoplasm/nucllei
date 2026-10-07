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
      ⑤ 保持期限 `SANDBOX_WORKSPACE_RETENTION_DAYS` (既定 0 = 消さない。>0 で N 日未使用の
      未 mount `.img` を削除。最終利用は `.images/<user>.last_used` sidecar。2026-10-03、ユニットテストのみ) /
      C9 ext4 brick 耐性 (破損 .img を退避 → fresh、atomic 生成) / C5 枯渇 503。
      exec/exec_cell 回帰なし・live_session ガード・C9 復旧を実機検証済
- [x] **API の共有トークン認証 (opt-in)** (#132。2026-09-04 実装、2026-09-05 に
      fail-closed → opt-in へ変更)。`SANDBOX_TOKEN` を**設定すれば** middleware が全パスで
      `Authorization: Bearer <SANDBOX_TOKEN>` を要求する (免除は `/healthz` `/metrics`
      のみ、比較は `hmac.compare_digest`)。**未設定なら認証は掛からず従来どおり動く** —
      起動は拒否せず warning を 1 行出すだけ (「キーが無ければ元のまま」、ken 判断)。
      呼び出し元 (nucllei) 側の登録ゲートは `SANDBOX_URL` のみで、トークンがあるときだけ
      ヘッダを付ける。回帰は `sandbox/tests/test_auth.py` +
      `nucllei/test/.../test_builtin_sandbox.py`。設計は README「セキュリティモデル」。
      **配備側 (llens) の追従は向こう持ち** = compose の 2 サービス (sandbox / nucllei) に
      同じ `SANDBOX_TOKEN` を渡す + 外部 GC スクリプトにヘッダを足す。Prometheus と
      healthcheck は免除なので変更不要。**トークンを渡さない配備は無認証のまま**なので、
      渡すこと自体が配備側の作業として残る

- [x] **参照用データの read-only bind** (#138)。コンテナの **`/ext` 直下に在るものが、同じ
      パスのまま jail へ `--bindmount_ro`** で出る (env は増やさない。置き場は 2026-10-03 に
      `/mnt` → `/ext`: nucllei の URL `/ext/<rel>` と同じ文字列にする。ken 決定)。空なら挙動不変、
      渡すと jail の起動ごと落ちる名前 (壊れた symlink・`:` 入り) は飛ばして警告、
      起動ログに bind 一覧を 1 行。回帰は
      `tests/test_ro_mounts.py` と `test_isolation.py` の `test_ro_mount_root_is_read_only`
      (volume を入れた配備でのみ実効)。**配備側 (llens) の追従は向こう持ち** = compose に
      volume を足す + モデルへの使い分け指示を prompt に書く
- [x] **レガシー Office (.doc/.xls/.ppt) を開く道具** (2026-10-02)。LibreOffice headless
      (writer/calc/impress の nogui 最小構成) + antiword + catdoc、venv に olefile /
      msoffcrypto-tool。soffice のプロファイルは wrapper で /tmp へ (rootfs ro・HOME=workspace
      対策)。jail 経由の変換を `test_isolation.py` に回帰として追加。**配備側 (llens) の追従は
      向こう持ち** = モデル向けの使い方 (skill / prompt)
- [x] **レガシーデータを開く軽い道具** (2026-10-02)。apt の別層に xxd / hexdump / mdbtools /
      unrtf / p7zip / unar / lhasa / cabextract / sqlite3 / xmllint / dos2unix / qpdf / IPA フォント
      (+ ＭＳ 明朝・ゴシック → IPA の fontconfig alias)、venv に dbfread / xlwt / extract-msg / hl7 /
      pyreadstat / pdfplumber / mojimoji / jpholiday。非圧縮 +約 100MB。ghostscript / imagemagick /
      tesseract は重いので見送り (別判断)。回帰は `test_isolation.py` の `test_legacy_data_*`。
      **未検証**: 日本語を含む実物の .mdb / .msg / .cab、圧縮 LZH。**配備側 (llens) の追従は
      向こう持ち** = モデル向けの使い分け (日本語名の書庫は unar、RTF は --html 経由 等)
- [x] **OCR・画像・研究データの道具** (2026-10-02、ken「軽くてよく使うものは入れておく。opencv も」)。
      apt の別層に tesseract (jpn / jpn_vert / eng) / ghostscript / libtiff-tools / zbar / exiftool /
      graphviz / tree / wdiff / mediainfo / git (identity は /etc/gitconfig)、venv に ocrmypdf ほか
      (README の表)。あわせて IPA 導入で和文の既定が IPA に倒れていたのを Noto に戻した
      (64-nucllei-fonts.conf)。回帰は `test_ocr_*` / `test_image_and_doc_*` / `test_research_*`。
      **配備側 (llens) の追従は向こう持ち** = モデル向けの使い分け (OCR は束の一括処理用 等)
- [x] **`imports/` `exports/` を期限付きキャッシュに** (#149、2026-10-07)。mtime から
      `SANDBOX_CACHE_TTL_DAYS` (既定 30) 日過ぎたファイルを idle workspace 回収の umount 直前に
      消す (symlink は辿らない・`memory/` 等には触らない)。回帰は `test_retention.py` の `test_cache_*`。
      値は `/files/write` `/exec` `/exec_cell` の応答 `cache_ttl_days` で返し、nucllei が取り込み・
      exports 自動添付の tool 出力に「YYYY-MM-DD 以降に消える」を添える (回帰は `test_files_list.py`)
- [x] **グループ・全体の共有の置き場** (#152、2026-10-07)。`/ext/groups/<g>` `/ext/public` を
      nucllei が渡す `mounts` どおりに 1 つずつ rw / ro で bind (丸ごとは bind しない)。kernel は
      bind の指紋が変わると作り直す。`/files/*` は `/ext/...` の絶対パスも受け、bind の中だけを
      経路の symlink を O_NOFOLLOW で拒否しながら扱う。回帰は `test_ext_mounts.py`。
      **実機 (コンテナ) での確認は未** — compose の volume 追加 (llens) 待ち
- [x] **ファイルブラウザ用の `/files/*`** (#150、2026-10-07)。`/files/ls` `stat` `delete` `rename`
      `mkdir` `usage` を追加、`/files/read` は `mtime` を返し `/files/write` は `expected_mtime`
      (違えば 409) / `exclusive` / `nofollow` を受ける。workspace でも `/ext` でも経路の symlink を
      O_NOFOLLOW で辿らない。容量切れは 507。回帰は `test_files_browser.py`。
      **実機 (コンテナ) での確認は未** — image の再ビルド・配備は統合側

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
