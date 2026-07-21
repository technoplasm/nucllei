<script lang="ts">
	// content_blocks(#17 L0)のブロック直接レンダリング。
	// details 擬似 HTML の再パースを経由せず、構造のまま描画する。
	// スキーマ: _memos/rendering-design.md §3「L0 の確定スキーマ」
	import ContentRenderer from './ContentRenderer.svelte';
	import ReasoningBlock from './ReasoningBlock.svelte';
	import ToolCallBlock from './ToolCallBlock.svelte';
	import type { Model } from '$lib/stores';
	import type { ChatHistory, ContentBlock, MessageSource } from '$lib/types/chat';
	import type { ToolCallRender } from '$lib/utils/tool-render';

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
	};

	let {
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

	// メッセージ内の reasoning / tool call ブロックの一括開閉。各ブロックの下辺
	// 「すべて開く / すべて閉じる」から呼ばれ、seq を進めて全ブロックに追従させる
	// (各ブロックの open はローカル state のままなので、個別の手動開閉は殺さない)。
	let expandAll = $state<{ seq: number; open: boolean }>({ seq: 0, open: false });
	const requestExpandAll = (open: boolean) => {
		expandAll = { seq: expandAll.seq + 1, open };
	};
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
					{expandAll}
					onExpandAll={requestExpandAll}
				/>
			{/each}
		{/if}
	{/each}
</div>
