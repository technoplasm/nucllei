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

	// 実行中(結果未着)の tool call を自動展開し、引数(コード)が伸びていく
	// 様子を見せる(#20 ステップ5)。結果が返ると畳まれ、ヘッダが
	// 「View Result from …」になる。設定 UI は増やさない — 挙動を変えたい
	// ときはこの定数を編集する(false = 常に閉じて開始、旧挙動)。
	const AUTO_EXPAND_EXECUTING_TOOL_CALLS = true;

	// content_blocks は既に構造化データ。文字列化して属性に詰め直さず、そのまま渡す
	// (旧 details 経路の「JSON.stringify → 属性文字列 → decode → 再パース」往復を廃止)。
	// call.function.arguments はモデル由来の JSON 文字列、result.content は文字列、
	// files は配列。表示側(Collapsible/HljsCode)がそのまま整形する。
	const toolCallAttributes = (call: any, results: any[]) => {
		const result = (results ?? []).find((r) => r.tool_call_id === call.id);
		const attributes: Record<string, any> = {
			type: 'tool_calls',
			done: result ? 'true' : 'false',
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
				className="w-full space-y-1 mb-1.5 text-sm"
			>
				<!--
					reasoning は markdown として解釈しない(#17 決定)。
					XSS 不変条件: reasoning は必ず Svelte のテキスト補間({block.content})で
					出すこと。{@html} に変えてはならない(9f4f 監査 2026-07-06: 生 <think>
					内容はモデル出力でありサニタイズ対象。テキスト補間なら構造的に無害)。
				-->
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
		{:else if block.type === 'skills'}
			<!-- `/command` 発火の表示(#35)。展開すると注入された指示が見える。
			     指示はモデル出力ではなく skill 作者の記述だが、reasoning と同じく
			     テキスト補間で出す({@html} 禁止)。 -->
			{#each block.skills ?? [] as skill (skill.command)}
				<Collapsible
					id={`${id}-block-${blockIdx}-skill-${skill.command}`}
					title=""
					open={false}
					attributes={{
						type: 'skills',
						done: 'true',
						command: skill.command,
						title: skill.title
					}}
					className="w-full space-y-1 mb-1.5 text-sm"
				>
					<div
						class="mb-1.5 text-sm text-gray-500 dark:text-gray-400 border-s-2 border-gray-200 dark:border-gray-800 ps-3 whitespace-pre-wrap"
						slot="content"
						dir="auto"
					>
						{skill.content}
					</div>
				</Collapsible>
			{/each}
		{:else if block.type === 'tool_calls'}
			{#each block.calls ?? [] as call (call.id)}
				<!-- open は「結果未着かどうか」で流れる: 実行中 true → 結果到着で
				     false に変わり自動で畳まれる。手動開閉は値が変わるまで優先される -->
				<Collapsible
					id={`${id}-block-${blockIdx}-${call.id}`}
					title=""
					open={AUTO_EXPAND_EXECUTING_TOOL_CALLS &&
						!(block.results ?? []).find((r) => r.tool_call_id === call.id)}
					attributes={toolCallAttributes(call, block.results)}
					className="w-full space-y-1 mb-1.5 text-sm"
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
