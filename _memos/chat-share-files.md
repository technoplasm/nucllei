# チャット共有とファイル権限(#41 真実源)

2026-07-10 仕様確定(議論の経緯は issue #41)。実装状況も #41 を参照。
関連: `permissions.md`(権限マトリクス)/ `file-handling.md`(#52 添付処理 — 別軸)。

## 原則

1. **権限は自動では「広がる」方向にしか動かない**。share は file の公開を広げるが、
   共有解除は狭めない。狭める(公開解除)・消す(削除)はすべて **owner の明示操作**
   (ファイル管理機構)に置く。暗黙の権限縮小を作らない — 「同じ file を参照する別の共有」
   「閲覧中のユーザー」との整合問題を根から断つ。
2. **file は実質 immutable**: upload 時に raw(storage `path`)が一度書かれ、
   Attachment Function(prism docling 等)の抽出テキストを core が `data.content` に一度焼く。
   編集 UI は無い。よって share は**参照のまま**で「共有時点の内容」が保たれる。
   実体コピーが要るのは clone だけ。

## share(参照 + public 化)

- **share 時**(`insert/update_shared_chat_by_chat_id`): 従来の chat スナップショットに加え、
  chat JSON 中の file_id を収集し **`file.access_control = {"read": {"public": true}}`** を set。
  `access_control` カラムは既存・現在未使用(KB 撤去で #40 ヘルパーの KB 経路が常に False)のため
  migration 不要。
- **read ゲート**(routers/files.py の全 read endpoint + `filter_files_by_user_access`):
  owner / admin / public マーカー。**原本(`/content`)も抽出テキスト(`/data/content`)も全部見せる**
  (共有=全部見せるの単純側。中途半端に絞らない)。
- **再共有**(スナップショット更新)時: file_id を集め直し、share 後に添付された新規分も public 化。
  share と同一コードパス。
- **共有解除では file の権限を触らない**(public のまま残す — 原則 1)。
  後始末はファイル管理機構で owner が行う。
- **owner による file 削除 = 事実上の共有(ファイル分)取り消し**。共有チャット側の参照は
  404 になってよい(参照方式の意味論として正しい)。

## clone(実体コピー)

- **`clone_shared_chat_by_id`**: chat JSON コピーに加え、参照 file を**実体コピー**
  (storage raw + `data`/`meta` を新 file_id で複製、**cloner 所有・`access_control=None`(private)**)し、
  clone 側 chat JSON 内の file_id を差し替える。
- clone した瞬間から元 file の生死・共有状態と無縁(共有解除・元 file 削除に耐える)。
- **admin の `ENABLE_ADMIN_CHAT_ACCESS` 経路**(生 chat_id からの clone)も同じコピーを通す。
- 自チャットの clone(`clone_chat_by_id`)は同一 owner の参照共有のままで可(コピー不要)。
- 割り切り: 抽出中(status 未確定)の file はその時点の状態でコピーされ、後から元の抽出が
  完了しても追従しない(稀・許容)。

## 権限フラグ(新設)

- **`chat.share` / `chat.clone`** を USER_PERMISSIONS に追加。
  enforcement は routers/chats.py の share / clone endpoint 入口(permissions.md 原則 5:
  サーバー側チェック + UI ミラー + マトリクス行 + ネガティブテスト をセットで)。
- 既定: **share=on / clone=off**(院内運用は share 中心、clone は稀)。

## ファイル管理機構(別トラック・#41 は非依存)

- owner が自分の file を **一覧・内容閲覧・削除・公開状態の確認と解除(public → private)** できる管理面。
  **公開を狭める唯一の場所**(原則 1 の受け皿)。
- #41 本体はこれに依存しない(管理機構が無い間は「広げっぱなし + file 削除で取り消し」で運用可能)。
- **issue #69**(起票済み・詳細設計はあとで)。#16 実行順への掲載は /sync-docs で。

## 副産物・掃除

- `has_access_to_file`(routers/files.py)の KB 依存を public マーカー判定へ置換
  → **`models/knowledge.py` を温存する最後の理由が消滅**(IDOR テスト書き換えとセットで撤去可)。
- permissions.md の chat / file 行・フラグ表を本仕様に合わせて改訂(実施済み)。

## 実装スコープ(#41)

1. share/再共有時の file public 化 + read ゲート改訂(endpoint + middleware filter)
2. clone の file 実体コピー(admin 経路含む)
3. `chat.share` / `chat.clone` フラグ + enforcement + UI ミラー
4. 回帰テスト: 共有 read の正/負、**解除後も read 可**(原則 1 の固定)、clone 後の独立性
   (元削除・共有解除に耐える)、フラグ off の負テスト
5. KB ヘルパー置換 + `models/knowledge.py` 撤去(別コミットで可)
