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

## ファイル管理機構(#69 — 2026-07-12 実装)

- owner が自分の file を **一覧・内容閲覧・削除・共有状態のフル編集** できる管理面(`/files` ページ)。
  **公開を狭める唯一の場所**(原則 1 の受け皿)であることは変わらないが、2026-07-12 の仕様確定で
  **owner による ACL フル編集(非公開 / グループ共有 / 全体公開 — 広げる方向も可)** に拡張した。
  チャット共有による自動 public 化(広がる方向)は従来どおり併存する。
- ACL 編集エンドポイント: `POST /files/{id}/access_control/update`(owner / admin のみ、他は 403)。
  受理形: `null`(非公開)/ `{"read": {"public": bool, "group_ids": [], "user_ids": []}}`。
- 併せて確定した関連仕様(詳細は `file-handling.md` と issue #69):
  - Attachment フックの per-model 割り当て(`attachmentIds`)は**廃止** → グローバル config
    `attachment.function_id` に一本化。
  - 既存ファイルのチャット**再添付**で Attachment フックが `__event__="attach"` で再発火
    (再抽出するかはフック内で判断。None 返却=既存 content 温存)。
  - **画像も files テーブル経由に統一**(base64 直入れ廃止)。chat JSON の正準形は
    `{"type":"image","id":...,"url":"/api/v1/files/{id}/content"}`。送信直前に middleware が
    内部 URL を base64 化(`utils/file_images.py`)。share/clone の file 連動も image に適用。
- `accessed_at` カラム新設(migration `d9f1c3b7a2e8`)。content 取得系 + `/process` でタッチ。

## 副産物・掃除

- `has_access_to_file`(routers/files.py)の KB 依存を public マーカー判定へ置換
  → **`models/knowledge.py` を温存する最後の理由が消滅**(IDOR テスト書き換えとセットで撤去可)。
- permissions.md の chat / file 行・フラグ表を本仕様に合わせて改訂(実施済み)。

## 実装スコープ(#41)— **全項目出荷済み(2026-07-10)**

1. [x] share/再共有時の file public 化 + read ゲート改訂(endpoint + middleware filter)— `7be6155c1`
2. [x] clone の file 実体コピー(admin 経路含む)— `7be6155c1`
3. [x] `chat.share` / `chat.clone` フラグ + enforcement + UI ミラー — `7be6155c1`
4. [x] 回帰テスト 11 件(`test_chat_share_files.py`): 共有 read の正/負、**解除後も read 可**
   (原則 1 の固定)、clone 後の独立性(元削除・共有解除に耐える)、フラグ off の負テスト
5. [x] KB ヘルパー置換 + `models/knowledge.py` 撤去(migration `a7c9e2f4b8d1` で
   knowledge テーブル drop)— `f6dc1e2e0`

検証: backend 全 238 passed(当時)+ 実機 E2E(share → `{"read":{"public":true}}` 焼き込み、
unshare 後も維持)。関連修正: `/default/permissions` の pydantic モデルに share/clone が
欠落していた件は #55 のコミット(`f7f607b12`)で是正。
