<script lang="ts">
	import Fuse from 'fuse.js';
	import { getContext } from 'svelte';

	const i18n = getContext('i18n');

	let {
		suggestionPrompts = [],
		className = '',
		inputValue = '',
		onSelect = () => {}
	}: {
		suggestionPrompts?: any[];
		className?: string;
		inputValue?: string;
		onSelect?: (content: string) => void;
	} = $props();

	const sortedPrompts = $derived([...(suggestionPrompts ?? [])].sort(() => Math.random() - 0.5));

	const fuse = $derived(
		new Fuse(sortedPrompts, {
			keys: ['content', 'title'],
			threshold: 0.5
		})
	);

	const filteredPrompts = $derived(
		inputValue.length > 500
			? []
			: inputValue.trim()
				? fuse.search(inputValue.trim()).map((result) => result.item)
				: sortedPrompts
	);
</script>

<div class="mb-1 flex gap-1 text-xs font-medium items-center text-content-muted">
	{#if filteredPrompts.length > 0}
		<span class="i-lucide-zap size-3.5 block"></span>
		{$i18n.t('Suggested')}
	{/if}
</div>

<div class="h-40 overflow-auto scrollbar-none {className} items-start">
	{#if filteredPrompts.length > 0}
		{#each filteredPrompts as prompt, idx (prompt.id || prompt.content)}
			<button
				class="waterfall flex flex-col flex-1 shrink-0 w-full justify-between
				       px-3 py-2 rounded-xl bg-transparent hover:bg-surface-hover group"
				style="animation-delay: {idx * 60}ms"
				onclick={() => onSelect(prompt.content)}
			>
				<div class="flex flex-col text-left">
					{#if prompt.title}
						<div class="font-medium text-content line-clamp-1">
							{prompt.title}
						</div>
						<div class="text-xs text-content-muted font-normal line-clamp-2">
							{prompt.content}
						</div>
					{:else}
						<div class="font-medium text-content line-clamp-2">
							{prompt.content}
						</div>
					{/if}
				</div>
			</button>
		{/each}
	{/if}
</div>

<style>
	/* Waterfall animation for the suggestions */
	@keyframes fadeInUp {
		0% {
			opacity: 0;
			transform: translateY(20px);
		}
		100% {
			opacity: 1;
			transform: translateY(0);
		}
	}

	.waterfall {
		opacity: 0;
		animation-name: fadeInUp;
		animation-duration: 200ms;
		animation-fill-mode: forwards;
		animation-timing-function: ease;
	}
</style>
