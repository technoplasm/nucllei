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

## 決定モデル(env 正・実行時は一時)
> **env が毎回の起動で必ず勝つ(正の源)。実行中の UI 変更は「その稼働中だけ効く一時オーバーライド」
> (確認用)で永続しない。再起動すると env が再び強制する。恒久変更の唯一の経路は env/compose。**

- **起動時**: 各 config = env(あれば)/ 無ければ hardcoded 既定。**DB は参照しない**。
- **実行中**: admin UI からの変更は **in-memory(`app.state.config`)の一時値**。挙動を即確認できる。
  **DB に永続しない**(`.save()` は撤去 or no-op)。
- **再起動**: env が上書きし直す。
- 帰結: env が silently 無視される挙動が消える / stale-DB 地雷が消える /
  `_OVERRIDE` 系は原理的に不要(源が1つ) / DB `config` テーブル依存を config 経路から外せる /
  「seed プロファイル」「export/import」(#30 の旧タスク)は **env/compose = config-as-code** に一本化され不要化。

## config の2バケツ仕分け(215 箇所の `PersistentConfig` を棚卸し)
- **A. 運用固定(実行時に触らせない)** → 素の `os.environ.get(...)`(設定オブジェクトにしない)。
  例: 接続 URL(SGLang base_url)・認証方式・エンジン種別。既に `ENABLE_ADMIN_EXPORT` 等はこの形。
- **B. 実行時に確認用で変えられると嬉しい** → env 既定 + in-memory 可変・**非永続**の設定オブジェクト。
  `PersistentConfig` を改名(`RuntimeConfig` / 単に `Config`)。DB 読み書きは持たない。

`ENABLE_PERSISTENT_CONFIG` は概念ごと撤去。

## UI(観測性)
- 実行時に変更はできるが「**再起動で env 既定に戻る(非永続)**」ことが分かる表示にする。
- Settings 掃除は同バッチで: **version 表示=残す** / update check=除去 / help・外部リンク=削除 /
  license=除去(feature-triage / #10 の Settings 決定と整合)。

## スコープ・進め方(CORE 注意)
- `PersistentConfig` は 215 箇所。**CORE(認証・接続・Filter・OpenAI 互換)に触れる**ので回帰テスト必須。
- 段階実装推奨: ①`RuntimeConfig`(env 既定 + in-memory 非永続)を用意し `.save()`/DB 読みを外す →
  ②A バケツを素の env に落とす棚卸し → ③`config` テーブル / `save_config` / `ENABLE_PERSISTENT_CONFIG`
  の除去 → ④UI の非永続表示。
- **影響(周知)**: 現在 admin UI で保存している設定は再起動で env 既定に戻る。durable にしたい値は
  compose/env に移す運用へ切替(横展開の環境構築も env/compose ファイルが正、が #30 の狙いに合致)。

## 未決/次
- B バケツに実際どれを残すかの棚卸しは実装時に確定(まず「全部 A 寄り、確認用に触りたいものだけ B」で洗う)。
- 「接続情報を UI 保存で残したい」等 durable な実行時編集の要望が出たら、その数個だけ
  env-override 型(env set 時は env 勝ち + UI ロック表示)を例外併用する余地は残す(現時点は不要と判断)。
