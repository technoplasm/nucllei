<script lang="ts">
	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import AddConnectionModal from '$lib/components/AddConnectionModal.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';

	import ManageOllamaModal from './ManageOllamaModal.svelte';

	let {
		url = $bindable(''),
		config = $bindable({}),
		idx = 0,
		onSubmit = () => {},
		onDelete = () => {}
	}: {
		url?: string;
		config?: any;
		idx?: number;
		onSubmit?: (connection: any) => void;
		onDelete?: () => void;
	} = $props();

	let showManageModal = $state(false);
	let showConfigModal = $state(false);
	let showDeleteConfirmDialog = $state(false);
</script>

<AddConnectionModal
	ollama
	edit
	bind:show={showConfigModal}
	connection={{
		url,
		key: config?.key ?? '',
		config: config
	}}
	onDelete={() => {
		showDeleteConfirmDialog = true;
	}}
	onSubmit={(connection: any) => {
		url = connection.url;
		config = { ...connection.config, key: connection.key };
		onSubmit(connection);
	}}
/>

<ConfirmDialog
	bind:show={showDeleteConfirmDialog}
	on:confirm={() => {
		onDelete();
		showConfigModal = false;
	}}
/>

<ManageOllamaModal bind:show={showManageModal} urlIdx={idx} />

<div class="flex gap-1.5">
	<Tooltip
		className="w-full relative"
		content={$i18n.t(`WebUI will make requests to "{{url}}/api/chat"`, {
			url
		})}
		placement="top-start"
	>
		{#if !(config?.enable ?? true)}
			<div class="absolute top-0 bottom-0 left-0 right-0 opacity-60 bg-surface z-10"></div>
		{/if}

		<input
			class="w-full text-sm px-2 py-1 bg-surface-raised rounded-xs outline-hidden"
			placeholder={$i18n.t('Enter URL (e.g. http://localhost:11434)')}
			bind:value={url}
		/>
	</Tooltip>

	<div class="flex gap-1">
		<Tooltip content={$i18n.t('Manage')} className="self-start">
			<button
				class="self-center p-1 bg-transparent hover:bg-surface-hover rounded-lg"
				aria-label={$i18n.t('Manage')}
				onclick={() => {
					showManageModal = true;
				}}
				type="button"
			>
				<span class="i-lucide-download size-4 block"></span>
			</button>
		</Tooltip>

		<Tooltip content={$i18n.t('Configure')} className="self-start">
			<button
				class="self-center p-1 bg-transparent hover:bg-surface-hover rounded-lg"
				aria-label={$i18n.t('Configure')}
				onclick={() => {
					showConfigModal = true;
				}}
				type="button"
			>
				<span class="i-lucide-settings size-4 block"></span>
			</button>
		</Tooltip>
	</div>
</div>
