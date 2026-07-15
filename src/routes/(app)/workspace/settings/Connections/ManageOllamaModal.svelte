<script lang="ts">
	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	import Modal from '$lib/components/common/Modal.svelte';
	import ManageOllama from '../Models/Manage/ManageOllama.svelte';

	let { show = $bindable(false), urlIdx = null }: { show?: boolean; urlIdx?: number | null } =
		$props();
</script>

<Modal size="sm" bind:show>
	<div>
		<div class=" flex justify-between text-content px-5 pt-4 pb-2">
			<div
				class="flex w-full justify-between items-center text-lg font-medium self-center font-primary"
			>
				<div class=" shrink-0">
					{$i18n.t('Manage Ollama')}
				</div>
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

		<div class="flex flex-col md:flex-row w-full px-5 pb-4 md:space-x-4 text-content">
			<ManageOllama {urlIdx} />
		</div>
	</div>
</Modal>
