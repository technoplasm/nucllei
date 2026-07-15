<script lang="ts">
	import { getContext, onMount } from 'svelte';
	const i18n = getContext('i18n');

	import { user } from '$lib/stores';

	import Modal from '$lib/components/common/Modal.svelte';
	import { getOllamaConfig } from '$lib/apis/ollama';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import ManageMultipleOllama from './Manage/ManageMultipleOllama.svelte';

	let { show = $bindable(false) }: { show?: boolean } = $props();

	let selected = $state<string | null>(null);
	let ollamaConfig = $state<any>(null);

	onMount(async () => {
		if ($user?.role === 'admin') {
			ollamaConfig = await getOllamaConfig();

			if (ollamaConfig) {
				selected = 'ollama';
				return;
			}

			selected = '';
		}
	});
</script>

<Modal size="sm" bind:show>
	<div>
		<div class=" flex justify-between text-content px-5 pt-4">
			<div class=" text-lg font-medium self-center font-primary">
				{$i18n.t('Manage Models')}
			</div>
			<button
				class="self-center"
				aria-label={$i18n.t('Close')}
				onclick={() => {
					show = false;
				}}
			>
				<span class="i-lucide-x size-5 block"></span>
			</button>
		</div>

		<div class="flex flex-col md:flex-row w-full px-3 pb-4 md:space-x-4 text-content">
			<div class=" flex flex-col w-full sm:flex-row sm:justify-center sm:space-x-6">
				{#if selected === ''}
					<div class=" py-5 text-content-subtle text-xs">
						<div>
							{$i18n.t('No inference engine with management support found')}
						</div>
					</div>
				{:else if selected !== null}
					<div class=" flex w-full flex-col">
						<div
							class="flex gap-1 scrollbar-none overflow-x-auto w-fit text-center text-sm font-medium rounded-full bg-transparent text-content"
						>
							<button
								class="min-w-fit rounded-full p-1.5 {selected === 'ollama'
									? ''
									: 'text-content-subtle hover:text-content'}"
								onclick={() => {
									selected = 'ollama';
								}}>{$i18n.t('Ollama')}</button
							>
						</div>

						<div class=" px-1.5 py-1">
							{#if selected === 'ollama'}
								<ManageMultipleOllama {ollamaConfig} />
							{/if}
						</div>
					</div>
				{:else}
					<div class=" py-5">
						<Spinner />
					</div>
				{/if}
			</div>
		</div>
	</div>
</Modal>
