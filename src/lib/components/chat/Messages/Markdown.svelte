<script lang="ts">
	import { replaceTokens, processResponseContent } from '$lib/utils';
	import { user } from '$lib/stores';

	// #22 L2: marked → markdown-it。setext(lheading)・参照リンク定義を文法ごと無効化。
	// トークンは marked 互換形に変換され、MarkdownTokens.svelte がそのまま消費する。
	import { lex } from '$lib/utils/markdown-it';
	// #22 L1: ストリーミング・ブロックセグメンタ。確定ブロックを凍結し tail のみ再パース。
	import { segmentMarkdown, closeUnclosedFences } from '$lib/utils/markdown/segmenter';
	import { collectCiteRefs, type CiteRef } from '$lib/utils/citations';
	import { onDestroy } from 'svelte';
	import { setCitationScope, type CitationScope } from './citations.svelte';

	import MarkdownTokens from './Markdown/MarkdownTokens.svelte';

	type Token = { type: string; [key: string]: any };
	type StableBlock = { src: string; tokens: Token[]; refs: CiteRef[] };

	let {
		id = '',
		content,
		model = null,
		fileNames = undefined,
		citations = null,
		citationSlot = 0,
		onTaskClick = () => {}
	}: {
		id?: string;
		content: string;
		model?: { name?: string } | null;
		/** そのチャットの添付 id → 表示名。本文の `[file_id]` をリンクにする(#146 簡易版) */
		fileNames?: Map<string, string>;
		/**
		 * メッセージ単位の引用の番号付け(#146 簡易版)。渡すと本文の `[/ext/…]` / `[file_id]` を
		 * 番号チップで描画し、このブロックの引用を citationSlot(メッセージ内のブロック番号)に書き込む。
		 * 渡さない場所(tool の結果・reasoning・ユーザー発言等)では表示名のリンクのまま
		 */
		citations?: CitationScope | null;
		citationSlot?: number;
		onTaskClick?: (payload: Record<string, any>) => void;
	} = $props();

	const processed = $derived(
		content ? replaceTokens(processResponseContent(content), model?.name, $user?.name) : ''
	);

	// L1 セグメンタ統合(#22 / #14):
	// 確定ブロック(追記単調で不変)は「ソースが前回と同一ならトークン配列を参照ごと
	// 再利用」することで MarkdownTokens が再レンダされず DOM が凍結される。末尾の
	// 未確定ブロックだけを毎フレーム再パースする。全文再パース O(n²) → 末尾のみ O(n)。
	// キャッシュは $derived.by のクロージャ外に素の変数で持つ(参照同一性の保持が目的)。
	let prevStableBlocks: StableBlock[] = [];
	// 添付の集合が変わったら確定ブロックのキャッシュも捨てる(`[file_id]` のリンク化が変わる)
	let prevFileNames: Map<string, string> | undefined = undefined;

	const makeStableBlock = (src: string, env: { fileNames?: Map<string, string> }) => {
		const tokens = lex(src, env);
		return { src, tokens, refs: collectCiteRefs(tokens) };
	};

	const segments = $derived.by(() => {
		const { stable, tail } = segmentMarkdown(processed);
		const env = { fileNames };
		if (fileNames !== prevFileNames) {
			prevStableBlocks = [];
			prevFileNames = fileNames;
		}

		// 確定ブロックはソース一致で前回のトークン(参照)を再利用 → 再レンダしない。
		// 追記時は先頭が不変なので新規確定ブロックのみ lex される(O(n) 化の要)。
		const stableBlocks: StableBlock[] = [];
		for (let i = 0; i < stable.length; i++) {
			const cached = prevStableBlocks[i];
			stableBlocks.push(
				cached && cached.src === stable[i] ? cached : makeStableBlock(stable[i], env)
			);
		}
		prevStableBlocks = stableBlocks;

		// tail は未確定。fence の仮閉じだけ施して毎フレーム再パース(ちらつきを末尾に局所化)。
		const tailTokens: Token[] = tail ? lex(closeUnclosedFences(tail), env) : [];

		return { stableBlocks, tailTokens };
	});

	// 番号付き引用: このブロックの引用(文書順)をメッセージのスコープへ。確定ブロックぶんは
	// キャッシュ済み、tail だけ毎フレーム拾い直す。番号は出てきた順なので本文が伸びても変わらない。
	// スコープはメッセージ単位で差し替わらない(context は初期値で足りる)
	// svelte-ignore state_referenced_locally
	setCitationScope(citations);
	const citeRefs = $derived(
		citations
			? [
					...segments.stableBlocks.flatMap((block) => block.refs),
					...collectCiteRefs(segments.tailTokens)
				]
			: []
	);
	$effect.pre(() => {
		citations?.set(citationSlot, citeRefs);
	});
	onDestroy(() => {
		citations?.clear(citationSlot);
	});
</script>

{#key id}
	{#each segments.stableBlocks as block, i (i)}
		<MarkdownTokens tokens={block.tokens} id={`${id}-s${i}`} {onTaskClick} />
	{/each}
	<MarkdownTokens tokens={segments.tailTokens} id={`${id}-tail`} {onTaskClick} />
{/key}
