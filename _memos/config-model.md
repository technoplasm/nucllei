# llens の設定モデル(config layer)— #30 確定

2026-07-07 決定。OWUI の `PersistentConfig`(env+DB 二層・seed-once・DB 勝ち)を廃し、
**env を単一の正の源(single source of truth)**とするモデルに寄せる。真実源は本メモ。
既定「値」は `llens-defaults.md`、本メモは「どう管理するか」の方。

## なぜ変えるか(PersistentConfig の捻れ)
`PersistentConfig(env_name, config_path, env_value)` の実挙動(`config.py`):

```py
if self.config_value is not None and ENABLE_PERSISTENT_CONFIG:
    self.value = self.config_value   # DB に値があれば DB が勝つ
else:
    self.value = env_value           # 無ければ env
```

- 初回起動 or UI 保存で値が DB(`config` テーブル)に入ると、**以後 env は無視される**。
  → 「env を変えたのに反映されない」事故の温床(#30 の背景)。
- 概念として2つを混ぜている: ①どこが正の源か(= デプロイ意図。ふつう env)/
  ②実行中に編集できるか + それを DB に永続するか。OWUI は②の DB を①より優先させた。
- 名前 "persistent" は DB 永続という実装都合を指し、意図(実行時に調整できる設定)を表していない。

## 決定モデル(env 正・起動時に再強制。DB は稼働中の可変ストア)
> **env が毎回の起動で必ず勝つ(正の源)。実行中の UI 変更は DB に載って稼働中は有効
> (複数ワーカーで共有)だが、次の起動で env が再び上書きする。恒久変更の唯一の経路は env/compose。**

**変えたのは優先順位だけ。DB 永続機構(config テーブル / `save` / import・export)は残す。**
(当初メモは「DB 永続ごと撤去」と書いていたが 2026-07-08 に撤回 — DB は稼働中の可変ストア/
複数ワーカー共有として残し、起動時に env が勝つようにするのが狙い。)

- **起動時**: 各 config の実効値 = env(あれば)/ 無ければ hardcoded 既定。
  **env が DB 値より優先**(旧来は「DB に値があれば DB 勝ち」= #30 のバグ。この分岐を撤去して反転)。
- **実行中**: admin UI からの変更は `app.state.config` に反映 + **DB へ永続**(`.save()`)。
  稼働中は有効で、Redis 経由で複数ワーカーに共有される(既存機構のまま)。
- **再起動**: env が実効値を上書きし直す(DB の実行時値は起動時には勝てない)。
- 帰結: env が silently 無視される挙動が消える / `ENABLE_PERSISTENT_CONFIG` トグルは不要(env が常に正)/
  `_OVERRIDE` 系・「seed プロファイル」も不要(源が1つ)。**export/import と config テーブルは温存**
  (撤去はしない)。

## 名称と任意の棚卸し
- `PersistentConfig` → **`RuntimeConfig`** に改名済み(「実行時に調整できる env 既定の設定」の意)。
  DB 永続は保持したまま。改名は名前だけ。
- `ENABLE_PERSISTENT_CONFIG` は撤去済み(env が常に正なので分岐不要)。
- (任意・後回し)運用固定の項目(接続 URL・認証方式・エンジン種別など、実行時に触らせたくないもの)を
  素の `os.environ.get(...)` に落として設定オブジェクトから外す棚卸しは、やるなら別途。
  env 優先が効いている以上、急ぎではない。

## UI(観測性)
- 実行時に変更はできる(稼働中は有効・DB に載る)が「**再起動で env 既定に戻る**」ことが分かる表示にする。
- Settings 掃除は同バッチで: **version 表示=残す** / update check=除去 / help・外部リンク=削除 /
  license=除去(feature-triage / #10 の Settings 決定と整合)。

## 実装(完了 = 798a7aa4c、2026-07-08)
- `RuntimeConfig.__init__` の優先順位を反転(DB 勝ち分岐を撤去 → env が必ず勝つ)+
  `ENABLE_PERSISTENT_CONFIG` 撤去 + `PersistentConfig`→`RuntimeConfig` 改名。
  `save()`/`update()` の DB 永続はそのまま維持。CORE(認証/Filter/authz/defaults)回帰 46 passed。
- **影響(周知)**: 現在 admin UI で保存している設定は再起動で env 既定に戻る。durable にしたい値は
  compose/env に移す運用へ切替(横展開の環境構築も env/compose ファイルが正、が #30 の狙いに合致)。

## 残(任意・後回し)
- Settings UI に「再起動で env に戻る」表示 + Settings 掃除(update check/help/license 除去・version 残す)。
- 運用固定項目の `os.environ` 直読み棚卸し(上記「名称と任意の棚卸し」参照。急ぎでない)。
- 「接続情報を UI 保存で durable に残したい」等の要望が出たら、その数個だけ env-override 型
  (env set 時は env 勝ち + UI ロック表示)を例外併用する余地は残す(現時点は不要)。
