# 権限モデル仕様(真実源)

2026-07-10 制定(issue #66)。#4(group 交差)は前提誤りで close — ユーザーは複数 group に
所属でき(`group.user_ids`)、権限は全所属 group の OR 合成で足りる。
実装状況は issue #66 を参照。本メモは「あるべき仕様」を書く(現状との差分は末尾のギャップ表)。

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
   skill = skill editor(共有レシピ帳: editor は全 skill を編集でき、use-ACL も編集できる)。
   例外は「tool_ids 付き skill = admin 専有」だけ(権力を運ぶのは tool。GHSA-4pcg 境界)。
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
| skill | read ACL(発火 = read ∩ model.skillIds) | editor | **editor(全 skill)** | **editor** | editor | tool_ids 付きは全操作 admin。新規の初期 ACL=None(全員使用可) |
| channel | read ACL(閲覧・投稿・自メッセージ削除) | admin | admin | admin | admin | 現状実装が既にこの形 |
| chat | owner のみ | 本人 | owner | − | owner + `chat.delete` | share_id 発行=owner、共有閲覧=認証ユーザー全員(匿名不可)。clone/file 参照は #41 |
| folder | owner のみ | 本人 | owner | − | owner + `chat.delete` | |
| memory | owner のみ | 本人 | owner | − | owner | #52 で素の SQL ストアに降格済み |
| file | #40 の access_control ヘルパー(owner / 参照可視) | 本人 | − | − | owner | `chat.file_upload` フラグ |
| API key | 本人 | 本人(ENABLE_API_KEY 時) | − | − | 本人 | endpoint allowlist は 57q6 対策済み |
| valves(tool/function 全体設定) | − | − | admin | − | − | 7429 対策済み(read も制限) |
| UserValves(per-user) | 本人 | 本人 | 本人 | − | 本人 | |
| User.info(#55 予定) | tool へ露出(read) | − | **admin のみ**(予定) | − | admin | **現状は本人が merge 上書き可 = G3**。#55 実装時に admin 専有へ |
| group | − | admin | admin | − | admin | 複数所属可。権限は OR 合成 |

## permission フラグ(サーバー側 enforcement 必須)

| フラグ | 意味 | enforcement 地点(あるべき) |
|---|---|---|
| `workspace.skills` | skill editor ロール | routers/skills.py 全 CRUD |
| `chat.delete` / `chat.edit` / `chat.file_upload` / `chat.temporary*` | チャット操作 | routers/chats.py・files.py(実装済み) |
| `chat.stt` / `chat.tts` / `chat.call` | 音声機能 | **routers/audio.py(未実装 = G1)** |
| `features.image_generation` | 画像生成 | **routers/images.py(未実装 = G1)** |
| `features.web_search` / `code_interpreter` | 機能 | 対応機能の endpoint(#51 / sandbox 整備時に) |
| 廃止: `workspace.models` / `workspace.tools` / `workspace.knowledge` 残骸 / `sharing.*` | − | #66 で撤去 |

## 既知のギャップ(2026-07-10 監査、→ 対応 issue)

- **G1(高)**: `chat.stt`/`chat.tts`/`features.image_generation` がフロントの UI 隠しのみで、
  audio/images endpoint はサーバー側未チェック → **#66 スコープに追加済み**
- **G2(中)**: task endpoints(generate_title 等)が model_id 存在チェックのみで
  check_model_access 無し → **#66 スコープに追加済み**
- **G3**: User.info を本人が `{**user.info, **form_data}` で自由に上書き可(users.py)。
  #55(authz マーカー)実装の前提として **admin 専有化が必須** → #55 に記録

## 検証方針

- マトリクスの各行(entity × action)に対応する回帰テストを持つ(CORE 規約)。
  特に「境界の外側」(user が admin 操作を叩く / read ACL 外のエンティティを使う)を必ず
  ネガティブテストで固定する。
- フロントの権限分岐を追加・変更するときは、対応するサーバー側チェックの存在を
  このマトリクスで確認してから書く(無ければ先にサーバーに入れる)。
