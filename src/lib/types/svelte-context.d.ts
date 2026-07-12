// llens(#70): getContext('i18n') の型付け。
// ほぼ全コンポーネントが `const i18n = getContext('i18n')` と書いており、素の getContext は
// unknown を返すため `$i18n.t(...)` が「Cannot use 'i18n' as a store」で大量エラー化していた。
// リテラルキー 'i18n' へのオーバーロードを宣言マージで足し、全コンポーネントを一括で型付けする
// (実体は src/lib/i18n/index.ts が setContext する i18next インスタンスの writable store)。
import type { i18n as i18nType } from 'i18next';
import type { Writable } from 'svelte/store';

declare module 'svelte' {
	export function getContext(key: 'i18n'): Writable<i18nType>;
}
