<script lang="ts">
	import { replaceTokens, processResponseContent } from '$lib/utils';
	import { user } from '$lib/stores';

	// #22 L2: marked → markdown-it。setext(lheading)・参照リンク定義を文法ごと無効化。
	// トークンは marked 互換形に変換され、MarkdownTokens.svelte がそのまま消費する。
	import { lex } from '$lib/utils/markdown-it';
	// #22 L1: ストリーミング・ブロックセグメンタ。確定ブロックを凍結し tail のみ再パース。
	import { segmentMarkdown, closeUnclosedFences } from '$lib/utils/markdown/segmenter';

	import MarkdownTokens from './Markdown/MarkdownTokens.svelte';

	type Token = { type: string; [key: string]: any };
	type StableBlock = { src: string; tokens: Token[] };

	let {
		id = '',
		content,
		model = null,
		sourceIds = [],
		onSourceClick = () => {},
		onTaskClick = () => {}
	}: {
		id?: string;
		content: string;
		model?: { name?: string } | null;
		sourceIds?: string[];
		onSourceClick?: (id: string, sourceId?: string) => void;
		onTaskClick?: (payload: Record<string, any>) => void;
	} = $props();

	const processed = $derived(
		content
			? replaceTokens(processResponseContent(content), sourceIds, model?.name, $user?.name)
			: ''
	);

	// L1 セグメンタ統合(#22 / #14):
	// 確定ブロック(追記単調で不変)は「ソースが前回と同一ならトークン配列を参照ごと
	// 再利用」することで MarkdownTokens が再レンダされず DOM が凍結される。末尾の
	// 未確定ブロックだけを毎フレーム再パースする。全文再パース O(n²) → 末尾のみ O(n)。
	// キャッシュは $derived.by のクロージャ外に素の変数で持つ(参照同一性の保持が目的)。
	let prevStableBlocks: StableBlock[] = [];

	const segments = $derived.by(() => {
		const { stable, tail } = segmentMarkdown(processed);

		// 確定ブロックはソース一致で前回のトークン(参照)を再利用 → 再レンダしない。
		// 追記時は先頭が不変なので新規確定ブロックのみ lex される(O(n) 化の要)。
		const stableBlocks: StableBlock[] = [];
		for (let i = 0; i < stable.length; i++) {
			const cached = prevStableBlocks[i];
			stableBlocks.push(
				cached && cached.src === stable[i] ? cached : { src: stable[i], tokens: lex(stable[i]) }
			);
		}
		prevStableBlocks = stableBlocks;

		// tail は未確定。fence の仮閉じだけ施して毎フレーム再パース(ちらつきを末尾に局所化)。
		const tailTokens: Token[] = tail ? lex(closeUnclosedFences(tail)) : [];

		return { stableBlocks, tailTokens };
	});
</script>

{#key id}
	{#each segments.stableBlocks as block, i (i)}
		<MarkdownTokens tokens={block.tokens} id={`${id}-s${i}`} {onTaskClick} {onSourceClick} />
	{/each}
	<MarkdownTokens tokens={segments.tailTokens} id={`${id}-tail`} {onTaskClick} {onSourceClick} />
{/key}
