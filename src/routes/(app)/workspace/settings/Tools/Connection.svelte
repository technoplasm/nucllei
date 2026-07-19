<script lang="ts">
	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import AddServerModal from '$lib/components/AddServerModal.svelte';

	let {
		connection = $bindable(null),
		onSubmit = () => {},
		onDelete = () => {}
	}: {
		connection?: any;
		onSubmit?: (connection: any) => void;
		onDelete?: () => void;
	} = $props();

	let showConfigModal = $state(false);
	let showDeleteConfirmDialog = $state(false);
</script>

<AddServerModal
	edit
	bind:show={showConfigModal}
	{connection}
	onDelete={() => {
		showDeleteConfirmDialog = true;
	}}
	onSubmit={(c: any) => {
		connection = c;
		onSubmit(c);
	}}
/>

<ConfirmDialog
	bind:show={showDeleteConfirmDialog}
	onConfirm={() => {
		onDelete();
		showConfigModal = false;
	}}
/>

<div class="flex w-full gap-2 items-center">
	<Tooltip
		className="w-full relative"
		content={$i18n.t(`WebUI will make requests to "{{url}}"`, {
			url: `${connection?.url}/${connection?.path ?? 'openapi.json'}`
		})}
		placement="top-start"
	>
		{#if !(connection?.config?.enable ?? true)}
			<div class="absolute top-0 bottom-0 left-0 right-0 opacity-60 bg-surface z-10"></div>
		{/if}
		<div class="flex w-full gap-2">
			<div class="flex-1 relative">
				<input
					class="w-full text-sm px-2 py-1 bg-surface-form rounded-xs outline-hidden"
					placeholder={$i18n.t('API Base URL')}
					bind:value={connection.url}
					autocomplete="off"
				/>
			</div>

			{#if (connection?.auth_type ?? 'bearer') === 'bearer'}
				<SensitiveInput
					outerClassName="flex flex-1 px-2 bg-surface-form rounded-xs"
					inputClassName="w-full text-sm py-1 placeholder:text-content-subtle bg-transparent outline-hidden"
					placeholder={$i18n.t('API Key')}
					bind:value={connection.key}
					required={false}
				/>
			{/if}
		</div>
	</Tooltip>

	<div class="flex gap-1">
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
