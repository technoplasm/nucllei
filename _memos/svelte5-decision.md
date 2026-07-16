# Svelte 4 → 5 移行判断(issue #26)

2026-07-05 決定。**採用: 互換モードでの段階移行(全体を Svelte 5 コンパイラに上げ、既存コンポーネントは Svelte 4 構文のまま、新規は runes で書く)。**

## 実測スパイクの結果(判断根拠)

svelte 5.56.4 / @sveltejs/vite-plugin-svelte 4.x / svelte-check 4.x に上げて計測:

- **アプリ全 334 `.svelte` ファイル中、コンパイルエラーは 5 ファイル・5 箇所のみ**
  - 文字列 `onload` 属性(`attribute_invalid_event_handler`)×2 — MarkdownInlineTokens / MarkdownTokens の iframe
  - `<button>` の入れ子(`node_invalid_placement`、もともと不正な HTML)×2 — ChannelItem / RecursiveFolder
  - store 名のシャドーイング(`store_invalid_scoped_subscription`)×1 — Chat.svelte の `chatCompletedHandler`/`chatActionHandler` の引数 `chatId`
- **Svelte 4 専用ライブラリは全て Svelte 5 コンパイラで legacy コンパイル可能**:
  bits-ui 0.19 / paneforge 0.0.6 / @xyflow/svelte 0.1 / svelte-sonner 0.3 / svelte-confetti 1.4 / @sveltejs/svelte-virtual-list 3.0。
  ライブラリのメジャーアップ(bits-ui 1.x 等 = runes 版・API 破壊)は**不要**
- 上記 5 箇所の修正後、`pnpm build`(client + SSR + adapter-static)が green
- ランタイム互換リスクの静的確認: 命令的コンポーネント API(`new Component()` / `$set` / `$destroy`)・
  `beforeUpdate`/`afterUpdate`・`svelte/internal` 依存はいずれも **0 件**。`<svelte:component>` は 4 件(legacy モードで動作、非推奨のみ)
- Cypress e2e(registration / settings)によるランタイムスモークで確認(結果はコミットメッセージ参照)

## 方針

1. develop に toolchain bump(svelte ^5 / vite-plugin-svelte ^4 / svelte-check ^4)+ 5 箇所の修正をコミットする。
   既存コンポーネントは **Svelte 4 構文のまま触らない**(触るときに runes 化を検討、義務ではない)
2. **新規コンポーネント(#14 レンダリング刷新トラック: セグメンタ描画・reasoning 折りたたみ・tool 引数ウィジェット)は runes で書く**。
   fine-grained reactivity は growing string の追記描画(L3)と相性が良い
3. ライブラリの runes 版メジャー(bits-ui 1.x 等)への追従は**しない**(現バージョンで動作している限り凍結。air-gapped・薄化方針とも整合)
4. upstream の Svelte 5 移行コードは参照しない(v0.6.6+ 禁止)。根拠は公式マイグレーションガイドのみ

## 留意点

- `pnpm check`(svelte-check 4)のエラー母数は v4.2/check3 時代の 5056 から変動する。CI 対象外のまま(#25 の残タスク)
- eslint-plugin-svelte 2.x / prettier-plugin-svelte 3.x は Svelte 5 対応版が存在するが、lint 再有効化(#25)まで保留
- 深い挙動差(untrack 相当のタイミング、束縛の伝播)はレンダリング刷新の新規コンポーネント側で顕在化しうる。
  L0〜L3 は fixture リプレイテスト(#29 の成果)で挙動固定する前提なので、そこで検出する

## 発見済みの互換モード regression(2026-07-05)

- **初期化時の `$: dispatch(...)` が親の `on:change` に届く**(Svelte 4 では届かず無害だった)。
  実害例: `common/Switch.svelte` 経由で workspace モデル一覧を開くだけに全モデルへ
  `/api/v1/models/model/toggle` が発火し、is_active が訪問ごとに反転していた
  (コミット 1a7e722b0 で Switch / Collapsible に「値が実変化したときだけ発火」ガードを導入)。
- 同型の芽: `createEventDispatcher` + `$:` の組み合わせは初期化発火を疑うこと。
  現存は Switch / Collapsible の 2 箇所のみ(`grep -rlF '$: dispatch(' src/`)。
  新規コンポーネントは runes で書くため対象外だが、upstream 由来コードを触るときは再 grep する。
  - **追記(2026-07-17): `$: dispatch(` は 0 件になった**(#70 A2-2 で `common/` の dispatcher を
    全廃し、Switch / Collapsible は callback prop 化 — lastState ガードパターンは CLAUDE.md に転記済み)。
    upstream 由来コードを触るときの再 grep 習慣は維持。

## 現況(2026-07-17 追記)

- 移行方針は実運用に昇格して CLAUDE.md 規約になった: **新規は runes + callback prop(`onXxx`)、
  `createEventDispatcher` 新規使用禁止、named slot も新規は snippet prop**。`common/` の dispatcher は
  2026-07-16 に全廃。残る dispatcher は 25 ファイル(chat メッセージ樹・サイドバー等)で #70 C トラック
  (分割・runes 化)で消化する。
- 実バージョン: svelte 5.56.4 / vite-plugin-svelte 4.0.4 / svelte-check 4.7.1(スパイク時と同じ系列)。
- legacy ライブラリ凍結は継続(bits-ui 0.19.7 / paneforge 0.0.6 / svelte-sonner 0.3.19 /
  svelte-confetti 1.4.0)。**@xyflow/svelte と svelte-virtual-list は依存ごと撤去済み**(薄化)。
- `<svelte:component>` はスパイク時 4 件 → **現存 1 件**(`chat/Messages/Markdown/AlertRenderer.svelte`)。
- svelte-check は **warnings 全プロジェクト 0**(2026-07-16 達成、以後 warning 新規 0 運用)・
  **errors 1274**。CI(GitHub Actions)には依然入れていない(#25 の残タスクのまま)。
