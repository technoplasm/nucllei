<script lang="ts">
	// content_blocks(#17 L0)のブロック直接レンダリング。
	// details 擬似 HTML の再パースを経由せず、構造のまま描画する。
	// スキーマ: _memos/rendering-design.md §3「L0 の確定スキーマ」
	import ContentRenderer from './ContentRenderer.svelte';
	import Collapsible from '$lib/components/common/Collapsible.svelte';
	import Markdown from './Markdown.svelte';
	import { settings } from '$lib/stores';

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
		save = false,
		onTaskClick = () => {},
		onSourceClick = () => {},
		onAddMessages = () => {},
		onUpdate = (blockIdx: number, detail: any) => {},
		onSelect = (detail: any) => {}
	} = $props();

	// tool call の自動展開(実行中だけ開く #20 ステップ5)は OFF にした
	// (2026-07-15): 開閉が実行状態で勝手に動くのをやめ、reasoning と同じく
	// expandDetails 設定を初期値に、手動の開閉をそのまま維持する。

	// content_blocks は既に構造化データ。文字列化して属性に詰め直さず、そのまま渡す
	// (旧 details 経路の「JSON.stringify → 属性文字列 → decode → 再パース」往復を廃止)。
	// call.function.arguments はモデル由来の JSON 文字列、result.content は文字列、
	// files は配列。表示側(Collapsible/HljsCode)がそのまま整形する。
	const toolCallAttributes = (call: any, results: any[]) => {
		const result = (results ?? []).find((r) => r.tool_call_id === call.id);
		const attributes: Record<string, any> = {
			type: 'tool_calls',
			done: result || done ? 'true' : 'false',
			id: call.id,
			name: call.function?.name ?? '',
			arguments: call.function?.arguments ?? ''
		};
		// 型駆動レンダリングのディスクリプタ(#20)。バックエンドが tool の型宣言
		// から抽出して call に同梱する。無ければ型に忠実なフォールバック描画。
		if (call.render) {
			attributes.render = call.render;
		}
		if (result) {
			attributes.result = result.content ?? '';
			if (result.files) {
				attributes.files = result.files;
			}
		}
		return attributes;
	};
</script>

<div class="w-full flex flex-col space-y-1.5">
	{#each blocks as block, blockIdx (blockIdx)}
		{#if block.type === 'reasoning'}
			<Collapsible
				id={`${id}-block-${blockIdx}`}
				title=""
				open={$settings?.expandDetails ?? false}
				attributes={{
					type: 'reasoning',
					done: block.done || done ? 'true' : 'false',
					duration: block.duration
				}}
				className="w-full space-y-1 text-sm"
			>
				<!--
					reasoning は本文と同じ Markdown コンポーネントで描画する。
					XSS 不変条件: 生 <think> 内容はモデル出力なのでサニタイズが必須。
					Markdown は MarkdownTokens/InlineTokens 内で DOMPurify.sanitize を通してから
					{@html} するため本文と同じ信頼モデルで安全。ここで直接 {@html} を書いてはならない。
				-->
				<div
					class="mb-1.5 text-sm text-content-muted border-s-2 border-gray-200 dark:border-gray-800 ps-3"
					slot="content"
					dir="auto"
				>
					<Markdown id={`${id}-block-${blockIdx}-reasoning`} content={block.content} />
				</div>
			</Collapsible>
		{:else if block.type === 'text'}
			<ContentRenderer
				id={`${id}-block-${blockIdx}`}
				{history}
				content={block.content}
				{sources}
				{floatingButtons}
				{save}
				{model}
				{onTaskClick}
				{onSourceClick}
				{onAddMessages}
				on:update={(e) => onUpdate(blockIdx, e.detail)}
				on:select={(e) => onSelect(e.detail)}
			/>
		{:else if block.type === 'tool_calls'}
			{#each block.calls ?? [] as call (call.id)}
				<Collapsible
					id={`${id}-block-${blockIdx}-${call.id}`}
					title=""
					open={$settings?.expandDetails ?? false}
					attributes={toolCallAttributes(call, block.results)}
					className="w-full space-y-1 text-sm"
				/>
			{/each}
		{/if}
	{/each}
</div>
