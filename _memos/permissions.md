# 権限モデル仕様(真実源)

2026-07-10 制定・同日実装完了(issue #66)。#4(group 交差)は前提誤りで close — ユーザーは
複数 group に所属でき(`group.user_ids`)、権限は全所属 group の OR 合成で足りる。
llens 拡張 `group_terms`(AND 交差)もコード・データとも撤去済み
(migration e8b3d6a1c5f7 が既存 ACL を read-only 形へ正規化)。
本メモの仕様と実装は一致している。回帰は `test_permissions_3tier.py` /
`test_access_control.py` / `test_skills.py` / `test_tools_authz.py`。

## 原則

1. **サーバーが唯一の enforcement point**。フロントの権限分岐は UX(導線を隠す)のためだけに
   存在し、セキュリティ境界ではない。**フロントだけでゲートする権限を作らない**(違反例が
   ギャップ G1)。フロントとサーバーの判定は同じ意味論(下のマトリクス)を参照する。
2. **3層ロール**: `admin` > `skill editor`(= `workspace.skills` 権限)> `user`。
   これ以外の階層(owner 特権・per-entity write ACL・workspace.models/tools)は廃止。
3. **「使えるか」は per-entity の read ACL に一本化**。
   ACL の形は `{"read": {"user_ids": [], "group_ids": []}}` のみ
   (`write` 側と `group_terms` は廃止)。`None` = 全員使用可、`{}` = 誰も使えない(admin 除く)。
4. **定義(作成・編集・削除)の権限はロールで決まる**: model / tool / function / channel = admin、
   skill = skill editor(**私有ノート + 全体閲覧** — 2026-07-10 改訂: 当初の「共有レシピ帳
   (editor が全 skill + use-ACL を編集)」案は、editor が勝手に public skill を作れてしまう
   ため撤回)。editor は全 skill を中身まで閲覧できるが、作成は必ず私有(ACL={} 固定)、
   編集・削除は自分の skill のみ、**ACL(公開・共有範囲)を触れるのは admin だけ**。
   tool_ids 付き skill は全操作 admin 専有(権力を運ぶのは tool。GHSA-4pcg 境界)。
5. **permission フラグ(`chat.*` / `features.*`)は必ずサーバー側 endpoint でチェックする**。
   フラグを新設したら enforcement(endpoint 側 has_permission)と UI ミラーを同時に入れ、
   マトリクスと回帰テストに行を足す。

## Entity × Action × Role マトリクス

read ACL = 「使える範囲」。`editor` = skill editor。○=可 / −=不可。

| entity | 使う(read) | 作成 | 内容編集 | ACL 編集 | 削除 | 備考 |
|---|---|---|---|---|---|---|
| model(preset) | read ACL(picker 表示) | admin | admin | admin | admin | |
| tool | read ACL(チャット実行時に per-tool 判定) | admin | admin | admin | admin | 実行時チェックは GHSA-4pcg の enforce_access_control |
| function(filter/action/pipe) | −(model 割り当てで発動、ACL 無し) | admin | admin | − | admin | |
| skill | read ACL(発火 = read ∩ model.skillIds)。**管理一覧の閲覧(中身込み)は editor 全件** | editor(**必ず私有 = ACL={} 固定**) | **owner のみ**(editor でも他人のは不可) | **admin のみ**(editor の保存では温存) | owner のみ | tool_ids 付きは全操作 admin。公開・共有は admin の後続操作でだけ広がる |
| ~~channel~~ | − | − | − | − | − | **DROP 決定(2026-07-10)** — 機能ごと撤去(#67) |
| chat | owner のみ | 本人 | owner | − | owner + `chat.delete` | share_id 発行=owner + `chat.share`、共有閲覧=認証ユーザー全員(匿名不可)、clone=`chat.clone`。file 連動は `chat-share-files.md`(#41 で確定) |
| folder | owner のみ | 本人 | owner | − | owner + `chat.delete` | |
| ~~memory~~ | − | − | − | − | − | **DROP 決定(2026-07-10)** — API ごと撤去(#67) |
| file | owner / admin / 共有 public マーカー / 明示 read ACL | 本人 | − | **owner + admin**(#69) | owner | `chat.file_upload` フラグ。share 時に `access_control.read.public=true`(共有解除でも自動では戻さない)。**#69(2026-07-12)でファイル管理機構が owner に ACL フル編集を開放**(非公開/グループ/全体公開 — 広げる操作も owner 可。エンドポイントは `POST /files/{id}/access_control/update`、非 owner・非 admin は 403)。file の ACL 意味論は他エンティティと逆: `None`=非公開(owner のみ)。詳細 `chat-share-files.md` |
| API key | 本人 | 本人(ENABLE_API_KEY 時) | − | − | 本人 | endpoint allowlist は 57q6 対策済み |
| valves(tool/function 全体設定) | − | − | admin | − | − | 7429 対策済み(read も制限) |
| UserValves(per-user) | 本人 | 本人 | 本人 | − | 本人 | |
| User.info(#55 実装済み) | tool へ露出(`__user__.info`) | − | **admin のみ** | − | admin | 宣言キーは `ADDITIONAL_USER_META`(env)。本人書き込み endpoint は撤去済み(G3 解消・2026-07-10) |
| group | − | admin | admin | − | admin | 複数所属可。権限は OR 合成 |

## permission フラグ(サーバー側 enforcement 必須)

| フラグ | 意味 | enforcement 地点(あるべき) |
|---|---|---|
| `workspace.skills` | skill editor ロール | routers/skills.py 全 CRUD |
| `chat.delete` / `chat.edit` / `chat.file_upload` | チャット操作 | routers/chats.py・files.py(file_upload は #69 で実装 — それまで UI ミラーのみで原則 1 違反だった。生成画像の内部保存 `store_uploaded_file` はゲート対象外) |
| `chat.share` / `chat.clone` | チャット共有・共有チャットの clone | routers/chats.py の share / clone endpoint(#41 で新設。既定 share=on / clone=off)|
| `features.image_generation` | 画像生成 | routers/images.py `/generations`(#66 で実装済み) |
| `features.web_search` / `code_interpreter` | 機能 | 対応機能の endpoint(#51 / sandbox 整備時に) |
| 廃止: `workspace.models` / `workspace.tools` / `workspace.knowledge` 残骸 / `sharing.*` | − | 撤去済み(#66、2026-07-10) |
| 廃止: `chat.stt` / `chat.tts` / `chat.call` | **STT/TTS は全員使用可**(2026-07-10 決定)。設定は admin、tool から呼ぶ内部 API を提供(#53) | フラグごと撤去済み(#66) |
| 廃止: `chat.temporary` / `chat.temporary_enforced` | temporary chat は機能ごと撤去(フロントは非表示済み、サーバー側も撤去 #67) | フラグごと撤去 |

## 既知のギャップ(2026-07-10 監査 → 全件解消)

- ~~**G1(高)**: `chat.stt`/`chat.tts`/`features.image_generation` がフロントの UI 隠しのみ~~
  → **解消(2026-07-10、#66 実装)**: stt/tts/call はフラグごと撤去(全員使用可)、
  image_generation は `/images/generations` に has_permission を追加
- ~~**G2(中)**: task endpoints が model_id 存在チェックのみ~~ → **解消(2026-07-10、#66 実装)**:
  `_check_task_model_access`(routers/tasks.py)で read ACL を検証(ACL 外は存在を明かさず 404)
- ~~**G3**: User.info を本人が自由に上書き可~~ → **解消(2026-07-10、#55 実装)**:
  本人向け `/user/info`・`/user/info/update` を撤去し admin 専有化(geolocation 機能ごと撤去)

## 検証方針

- マトリクスの各行(entity × action)に対応する回帰テストを持つ(CORE 規約)。
  特に「境界の外側」(user が admin 操作を叩く / read ACL 外のエンティティを使う)を必ず
  ネガティブテストで固定する。
- フロントの権限分岐を追加・変更するときは、対応するサーバー側チェックの存在を
  このマトリクスで確認してから書く(無ければ先にサーバーに入れる)。
