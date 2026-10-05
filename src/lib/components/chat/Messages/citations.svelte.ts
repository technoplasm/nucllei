// メッセージ単位の番号付き引用(#146 の簡易版、2026-10-05)。
//
// ResponseMessage が 1 つ作り、本文のテキストブロック(ContentBlocks → ContentRenderer → Markdown)
// が「自分のブロック番号(slot)の引用列」を書き込む。番号はブロック番号順に通しで振る
// (tool 呼び出しを挟んだ前後の text も 1 つの番号列)。本文のチップ(MarkdownInlineTokens)と
// メッセージ下の一覧(CitationList)が同じ番号を読む。
// 番号付けそのものは純関数 `$lib/utils/citations` の numberCitations。
import { getContext, setContext } from 'svelte';
import { SvelteMap } from 'svelte/reactivity';

import { numberCitations, type CiteRef } from '$lib/utils/citations';

const sameRefs = (a: CiteRef[] | undefined, b: CiteRef[]) =>
	!!a && a.length === b.length && a.every((ref, i) => ref.href === b[i].href);

export class CitationScope {
	#slots = new SvelteMap<number, CiteRef[]>();

	readonly current = $derived(
		numberCitations([...this.#slots.entries()].sort(([a], [b]) => a - b).map(([, refs]) => refs))
	);

	/** チップに hover 中の番号(一覧の該当行をハイライトする) */
	active = $state<number | null>(null);

	set(slot: number, refs: CiteRef[]) {
		if (refs.length === 0) {
			this.clear(slot);
		} else if (!sameRefs(this.#slots.get(slot), refs)) {
			this.#slots.set(slot, refs);
		}
	}

	clear(slot: number) {
		if (this.#slots.has(slot)) this.#slots.delete(slot);
	}

	numberOf(key: string): number | undefined {
		return this.current.numbers.get(key);
	}
}

// Markdown が自分の子孫(MarkdownInlineTokens)へ渡す。番号を振らない Markdown(tool の結果・
// reasoning・ユーザー発言等)は null を置き、外側のスコープを遮る
const CITATION_SCOPE = Symbol('citation-scope');

export const setCitationScope = (scope: CitationScope | null) => setContext(CITATION_SCOPE, scope);

export const getCitationScope = () => getContext<CitationScope | null>(CITATION_SCOPE) ?? null;
