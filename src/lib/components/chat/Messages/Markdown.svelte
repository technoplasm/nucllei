<script>
	import { replaceTokens, processResponseContent } from '$lib/utils';
	import { user } from '$lib/stores';

	// #22 L2: marked → markdown-it。setext(lheading)・参照リンク定義を文法ごと無効化。
	// トークンは marked 互換形に変換され、MarkdownTokens.svelte がそのまま消費する。
	import { lex } from '$lib/utils/markdown-it';
	// #22 L1: ストリーミング・ブロックセグメンタ。確定ブロックを凍結し tail のみ再パース。
	import { segmentMarkdown, closeUnclosedFences } from '$lib/utils/markdown/segmenter';

	import MarkdownTokens from './Markdown/MarkdownTokens.svelte';
	import { createEventDispatcher } from 'svelte';

	const dispatch = createEventDispatcher();

	export let id = '';
	export let content;
	export let model = null;
	export let save = false;

	export let sourceIds = [];

	export let onSourceClick = () => {};
	export let onTaskClick = () => {};

	// L1 セグメンタ統合(#22 / #14):
	// 確定ブロック(追記単調で不変)は「ソースが前回と同一ならトークン配列を参照ごと
	// 再利用」することで MarkdownTokens が再レンダされず DOM が凍結される。末尾の
	// 未確定ブロックだけを毎フレーム再パースする。全文再パース O(n²) → 末尾のみ O(n)。
	let stableBlocks = []; // { src: string, tokens: Token[] }[]
	let tailTokens = [];

	$: updateSegments(
		content
			? replaceTokens(processResponseContent(content), sourceIds, model?.name, $user?.name)
			: ''
	);

	function updateSegments(src) {
		const { stable, tail } = segmentMarkdown(src);

		// 確定ブロックはソース一致で前回のトークン(参照)を再利用 → 再レンダしない。
		// 追記時は先頭が不変なので新規確定ブロックのみ lex される(O(n) 化の要)。
		const next = [];
		for (let i = 0; i < stable.length; i++) {
			const cached = stableBlocks[i];
			next.push(
				cached && cached.src === stable[i] ? cached : { src: stable[i], tokens: lex(stable[i]) }
			);
		}
		stableBlocks = next;

		// tail は未確定。fence の仮閉じだけ施して毎フレーム再パース(ちらつきを末尾に局所化)。
		tailTokens = tail ? lex(closeUnclosedFences(tail)) : [];
	}
</script>

{#key id}
	{#each stableBlocks as block, i (i)}
		<MarkdownTokens
			tokens={block.tokens}
			id={`${id}-s${i}`}
			{save}
			{onTaskClick}
			{onSourceClick}
			on:update={(e) => {
				dispatch('update', e.detail);
			}}
			on:code={(e) => {
				dispatch('code', e.detail);
			}}
		/>
	{/each}
	<MarkdownTokens
		tokens={tailTokens}
		id={`${id}-tail`}
		{save}
		{onTaskClick}
		{onSourceClick}
		on:update={(e) => {
			dispatch('update', e.detail);
		}}
		on:code={(e) => {
			dispatch('code', e.detail);
		}}
	/>
{/key}
