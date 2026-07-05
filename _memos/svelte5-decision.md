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
