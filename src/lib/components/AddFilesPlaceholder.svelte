<script lang="ts">
	import { getContext, type Snippet } from 'svelte';

	let {
		title = '',
		content = '',
		children
	}: { title?: string; content?: string; children?: Snippet } = $props();
	const i18n = getContext('i18n');
</script>

<div class="px-3">
	<div class="text-center text-6xl mb-3">📄</div>
	<div class="text-center dark:text-white text-xl font-semibold z-50">
		{#if title}
			{title}
		{:else}
			{$i18n.t('Add Files')}
		{/if}
	</div>

	{#if children}
		{@render children()}
	{:else}
		<div class="px-2 mt-2 text-center text-sm text-content-muted w-full">
			{#if content}
				{content}
			{:else}
				{$i18n.t('Drop any files here to add to the conversation')}
			{/if}
		</div>
	{/if}
</div>
