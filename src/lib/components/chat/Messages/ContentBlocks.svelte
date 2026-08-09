<script lang="ts">
	// content_blocks(#17 L0)のブロック直接レンダリング。
	// details 擬似 HTML の再パースを経由せず、構造のまま描画する。
	// スキーマ: _memos/rendering-design.md §3「L0 の確定スキーマ」
	import { getContext } from 'svelte';
	import ContentRenderer from './ContentRenderer.svelte';
	import ReasoningBlock from './ReasoningBlock.svelte';
	import TurnIndicator from './TurnIndicator.svelte';
	import ToolCallBlock from './ToolCallBlock.svelte';
	import { collapseAllSignal, requestCollapseAll } from './collapse-all.svelte';
	import type { Model } from '$lib/stores';
	import { getToolProgress } from '$lib/stores/tool-progress.svelte';
	import type { ChatHistory, ContentBlock, MessageSource } from '$lib/types/chat';
	import type { ToolCallRender } from '$lib/utils/tool-render';

	const i18n = getContext('i18n');

	// call.function.arguments はモデル由来の JSON 文字列(chat-events.ts では unknown 扱い)
	type ToolCall = {
		id: string;
		function?: { name?: string; arguments?: string };
		render?: ToolCallRender;
	};
	type ToolResult = {
		tool_call_id: string;
		content?: string;
		render?: { kind: string };
		// display チャネル(#88): tool 発の人間向け表示。そのまま ToolCallBlock へ渡す。
		display?: { kind: string; content: string }[];
		// 所要時間(#100、秒)。実行中の進捗が消えた後に残るのはこれ
		duration?: number;
	};

	let {
		// message.id。ブロック id の前置に使うほか、実行中 tool の進捗(#100)を
		// 引くキーの一部にもなる(tool_call_id はメッセージをまたぐと衝突しうる)
		id,
		blocks,
		// メッセージ確定後(中断含む)は block.done が立っていなくてもスピナーを
		// 止める保険。backend が finalize できずに死んだ場合、done=false のブロックが
		// DB に残る — ぐるぐるは「task が生きている間だけ」の規律をここで担保する。
		done = false,
		history,
		model = null,
		sources = null,
		floatingButtons = false,
		onTaskClick = () => {},
		onSourceClick = () => {},
		onAddMessages = () => {}
	}: {
		id: string;
		blocks: ContentBlock[];
		done?: boolean;
		history: ChatHistory;
		model?: Model | null;
		sources?: MessageSource[] | null;
		floatingButtons?: boolean;
		onTaskClick?: (payload: Record<string, unknown>) => void;
		onSourceClick?: (id: string, idx?: number | string) => void;
		onAddMessages?: (payload: {
			modelId: string;
			parentId: string;
			messages: { role: string; content: string }[];
		}) => void;
	} = $props();

	// content_blocks は既に構造化データ。文字列化して属性に詰め直さず、そのまま
	// 専用コンポーネント(ReasoningBlock / ToolCallBlock)へ渡す(旧 details 経路の
	// 「JSON.stringify → 属性文字列 → decode → 再パース」往復も、汎用 Collapsible の
	// attributes 経由レンダリングも廃止)。
	const toolCalls = (block: ContentBlock): ToolCall[] => (block.calls ?? []) as ToolCall[];

	const toolResult = (call: ToolCall, block: ContentBlock): ToolResult | null =>
		((block.results ?? []) as ToolResult[]).find((r) => r.tool_call_id === call.id) ?? null;

	// reasoning / tool call ブロックの一括開閉。各ブロックの下辺「すべて閉じる」から
	// 呼ばれ、seq を進めて全ブロックに追従させる(各ブロックの open はローカル state の
	// ままなので、個別の手動開閉は殺さない)。合図はメッセージ単位ではなく
	// チャット単位 — 押した通だけでなく、表示中の会話で開いているものを全部畳む。
	const expandAll = collapseAllSignal;
	const requestExpandAll = requestCollapseAll;

	// tool 実行完了 → 追撃ストリーム初着(prefill 待ち)の間は emit すべきデルタが
	// backend に無く、画面に動く要素がゼロになってフリーズに見える。この区間だけ
	// TurnIndicator に「Processing results...」のラベルを付ける。判定は「最後に描画される
	// block が results 充足済みの tool_calls」— 実行中は ToolCallBlock 自身の
	// Executing 表示があるので対象外、reasoning/text が届いた瞬間に自然に外れる。
	const awaitingFollowup = $derived.by(() => {
		if (done) return false;
		for (let i = blocks.length - 1; i >= 0; i--) {
			const block = blocks[i];
			// 空 text block は描画スキップ(下の each と同じ規律)— 判定でも無視する
			if (block.type === 'text' && (block.content ?? '').trim() === '') continue;
			return (
				block.type === 'tool_calls' &&
				toolCalls(block).length > 0 &&
				toolCalls(block).every((call) => toolResult(call, block) !== null)
			);
		}
		return false;
	});
</script>

<div class="w-full flex flex-col space-y-3">
	{#each blocks as block, blockIdx (blockIdx)}
		{#if block.type === 'reasoning'}
			<ReasoningBlock
				id={`${id}-block-${blockIdx}`}
				content={block.content ?? ''}
				done={block.done || done}
				duration={block.duration ?? null}
				{expandAll}
				onExpandAll={requestExpandAll}
			/>
		{:else if block.type === 'text' && (block.content ?? '').trim() !== ''}
			<!-- 空の text ブロック(tool 前後の空行だけ等)は描画しない —
			     space-y が空要素にも効いてブロック間が二重に開くため -->
			<ContentRenderer
				id={`${id}-block-${blockIdx}`}
				{history}
				content={block.content ?? ''}
				{sources}
				{floatingButtons}
				{model}
				{onTaskClick}
				{onSourceClick}
				{onAddMessages}
			/>
		{:else if block.type === 'tool_calls'}
			{#each toolCalls(block) as call (call.id)}
				<ToolCallBlock
					id={`${id}-block-${blockIdx}-${call.id}`}
					{call}
					result={toolResult(call, block)}
					{done}
					progress={getToolProgress(id, call.id)}
					{expandAll}
					onExpandAll={requestExpandAll}
				/>
			{/each}
		{/if}
	{/each}
	{#if !done}
		<!-- ターン進行中は常駐(#102)— live usage(donut + current/total + tok/s + 経過時間)を
		     出し続け、done で消える(揮発)。ラベルは追撃待ち(awaitingFollowup)のときだけ -->
		<TurnIndicator
			messageId={id}
			label={awaitingFollowup ? $i18n.t('Processing results...') : null}
		/>
	{/if}
</div>
