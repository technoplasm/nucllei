<script lang="ts">
	// content_blocks(#17 L0)のブロック直接レンダリング。
	// details 擬似 HTML の再パースを経由せず、構造のまま描画する。
	// スキーマ: _memos/rendering-design.md §3「L0 の確定スキーマ」
	import ContentRenderer from './ContentRenderer.svelte';
	import Collapsible from '$lib/components/common/Collapsible.svelte';
	import { settings } from '$lib/stores';

	let {
		id,
		blocks,
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

	const toolCallAttributes = (call: any, results: any[]) => {
		const result = (results ?? []).find((r) => r.tool_call_id === call.id);
		const attributes: Record<string, any> = {
			type: 'tool_calls',
			done: result ? 'true' : 'false',
			id: call.id,
			name: call.function?.name ?? '',
			arguments: JSON.stringify(call.function?.arguments ?? '')
		};
		if (result) {
			attributes.result = JSON.stringify(result.content ?? '');
			if (result.files) {
				attributes.files = JSON.stringify(result.files);
			}
		}
		return attributes;
	};
</script>

<div class="w-full flex flex-col">
	{#each blocks as block, blockIdx (blockIdx)}
		{#if block.type === 'reasoning'}
			<Collapsible
				id={`${id}-block-${blockIdx}`}
				title=""
				open={$settings?.expandDetails ?? false}
				attributes={{
					type: 'reasoning',
					done: block.done ? 'true' : 'false',
					duration: block.duration
				}}
				className="w-full space-y-1 mb-1.5"
			>
				<!-- reasoning は markdown として解釈しない(#17 決定) -->
				<div
					class="mb-1.5 text-sm text-gray-500 dark:text-gray-400 border-s-2 border-gray-200 dark:border-gray-800 ps-3 whitespace-pre-wrap"
					slot="content"
					dir="auto"
				>
					{block.content}
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
					open={false}
					attributes={toolCallAttributes(call, block.results)}
					className="w-full space-y-1 mb-1.5"
				/>
			{/each}
		{:else if block.type === 'code_interpreter'}
			<!-- llens 既定では無効の面(#11)。最低限の表示のみ -->
			<ContentRenderer
				id={`${id}-block-${blockIdx}`}
				{history}
				content={`\`\`\`python\n${block.content}\n\`\`\``}
				{model}
				floatingButtons={false}
				save={false}
			/>
		{/if}
	{/each}
</div>
