<script lang="ts">
	// 汎用の開閉コンテナ(children = 常時表示のヘッダ、content = 開時の中身)。
	// reasoning / tool call の表示は専用コンポーネント
	// (chat/Messages/ReasoningBlock.svelte / ToolCallBlock.svelte)へ分離済み —
	// ここに attributes 経由のドメイン分岐を書き戻さない。
	import { type Snippet } from 'svelte';

	import { slide } from 'svelte/transition';
	import { quintOut } from 'svelte/easing';

	let {
		open = $bindable(false),
		className = '',
		buttonClassName = 'w-fit text-content-muted hover:text-content transition',
		id = '',
		chevron = false,
		grow = false,
		disabled = false,
		hide = false,
		onChange = undefined,
		children,
		content
	}: {
		open?: boolean;
		className?: string;
		buttonClassName?: string;
		id?: string;
		chevron?: boolean;
		grow?: boolean;
		disabled?: boolean;
		hide?: boolean;
		onChange?: (open: boolean) => void;
		children?: Snippet;
		content?: Snippet;
	} = $props();

	// Switch.svelte と同じガード: 初期値では発火させず、
	// open が実際に変化したときだけ onChange を呼ぶ。
	let lastOpen = open;
	$effect(() => {
		if (open !== lastOpen) {
			lastOpen = open;
			onChange?.(open);
		}
	});
</script>

<div {id} class={className}>
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<!-- svelte-ignore a11y_click_events_have_key_events -->
	<div
		class="{buttonClassName} cursor-pointer"
		onpointerup={() => {
			if (!disabled) {
				open = !open;
			}
		}}
	>
		<div>
			<div class="flex items-start justify-between">
				{@render children?.()}

				{#if chevron}
					<div class="flex self-start translate-y-1">
						{#if open}
							<span class="i-lucide-chevron-up size-3.5 block"></span>
						{:else}
							<span class="i-lucide-chevron-down size-3.5 block"></span>
						{/if}
					</div>
				{/if}
			</div>

			{#if grow}
				{#if open && !hide}
					<div
						transition:slide={{ duration: 300, easing: quintOut, axis: 'y' }}
						onpointerup={(e) => {
							e.stopPropagation();
						}}
					>
						{@render content?.()}
					</div>
				{/if}
			{/if}
		</div>
	</div>

	{#if !grow}
		{#if open && !hide}
			<div transition:slide={{ duration: 300, easing: quintOut, axis: 'y' }}>
				{@render content?.()}
			</div>
		{/if}
	{/if}
</div>
