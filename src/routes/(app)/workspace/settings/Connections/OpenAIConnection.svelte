<script lang="ts">
	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import AddConnectionModal from '$lib/components/AddConnectionModal.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';

	let {
		url = $bindable(''),
		key = $bindable(''),
		config = $bindable({}),
		pipeline = false,
		onSubmit = () => {},
		onDelete = () => {}
	}: {
		url?: string;
		key?: string;
		config?: any;
		pipeline?: boolean;
		onSubmit?: (connection: any) => void;
		onDelete?: () => void;
	} = $props();

	let showConfigModal = $state(false);
	let showDeleteConfirmDialog = $state(false);
</script>

<ConfirmDialog
	bind:show={showDeleteConfirmDialog}
	on:confirm={() => {
		onDelete();
	}}
/>

<AddConnectionModal
	edit
	bind:show={showConfigModal}
	connection={{
		url,
		key,
		config
	}}
	onDelete={() => {
		showDeleteConfirmDialog = true;
	}}
	onSubmit={(connection: any) => {
		url = connection.url;
		key = connection.key;
		config = connection.config;
		onSubmit(connection);
	}}
/>

<div class="flex w-full gap-2 items-center">
	<Tooltip
		className="w-full relative"
		content={$i18n.t(`WebUI will make requests to "{{url}}/chat/completions"`, {
			url
		})}
		placement="top-start"
	>
		{#if !(config?.enable ?? true)}
			<div class="absolute top-0 bottom-0 left-0 right-0 opacity-60 bg-surface z-10"></div>
		{/if}
		<div class="flex w-full gap-2">
			<div class="flex-1 relative">
				<input
					class="w-full text-sm px-2 py-1 bg-surface-raised rounded-xs outline-hidden {pipeline
						? 'pr-8'
						: ''}"
					placeholder={$i18n.t('API Base URL')}
					bind:value={url}
					autocomplete="off"
				/>

				{#if pipeline}
					<div class=" absolute top-1/2 -translate-y-1/2 right-2.5">
						<Tooltip content="Pipelines">
							<span class="i-lucide-layers size-4 block"></span>
						</Tooltip>
					</div>
				{/if}
			</div>

			<SensitiveInput
				outerClassName="flex flex-1 px-2 bg-surface-raised rounded-xs"
				inputClassName="w-full text-sm py-1 placeholder:text-content-subtle bg-transparent outline-hidden"
				placeholder={$i18n.t('API Key')}
				bind:value={key}
			/>
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
