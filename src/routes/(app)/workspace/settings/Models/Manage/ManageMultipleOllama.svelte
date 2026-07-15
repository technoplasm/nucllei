<script lang="ts">
	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	import ManageOllama from './ManageOllama.svelte';

	let { ollamaConfig = null }: { ollamaConfig?: any } = $props();

	let selectedUrlIdx = $state(0);
</script>

{#if ollamaConfig}
	<!-- モーダル内(raised 相当面)のコントロールは 2 段ルールで surface-muted -->
	<div class="flex-1 mb-2.5 pr-1.5 rounded-xs bg-surface-muted text-content">
		<select
			class="w-full py-2 px-4 text-sm outline-hidden bg-transparent"
			bind:value={selectedUrlIdx}
			placeholder={$i18n.t('Select an Ollama instance')}
		>
			{#each ollamaConfig.OLLAMA_BASE_URLS as url, idx}
				<option value={idx}>{url}</option>
			{/each}
		</select>
	</div>

	<ManageOllama urlIdx={selectedUrlIdx} />
{/if}
