<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { getContext, onMount } from 'svelte';
	const i18n = getContext('i18n');

	import { verifyOpenAIConnection } from '$lib/apis/openai';
	import { verifyOllamaConnection } from '$lib/apis/ollama';

	import Modal from '$lib/components/common/Modal.svelte';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Switch from '$lib/components/common/Switch.svelte';

	let {
		show = $bindable(false),
		edit = false,
		ollama = false,
		direct = false,
		connection = null,
		onSubmit = () => {},
		onDelete = () => {}
	}: {
		show?: boolean;
		edit?: boolean;
		ollama?: boolean;
		direct?: boolean;
		connection?: any;
		onSubmit?: (connection: any) => void | Promise<void>;
		onDelete?: () => void;
	} = $props();

	let url = $state('');
	let key = $state('');

	let prefixId = $state('');
	let enable = $state(true);

	let modelId = $state('');
	let modelIds = $state<string[]>([]);

	let loading = $state(false);

	const verifyOllamaHandler = async () => {
		const res = await verifyOllamaConnection(url, key).catch((error) => {
			toast.error(`${error}`);
		});

		if (res) {
			toast.success($i18n.t('Server connection verified'));
		}
	};

	const verifyOpenAIHandler = async () => {
		const res = await verifyOpenAIConnection(url, key, direct).catch((error) => {
			toast.error(`${error}`);
		});

		if (res) {
			toast.success($i18n.t('Server connection verified'));
		}
	};

	const verifyHandler = () => {
		if (ollama) {
			verifyOllamaHandler();
		} else {
			verifyOpenAIHandler();
		}
	};

	const addModelHandler = () => {
		if (modelId) {
			modelIds = [...modelIds, modelId];
			modelId = '';
		}
	};

	const submitHandler = async () => {
		loading = true;

		if (!ollama && !url) {
			loading = false;
			toast.error('URL is required');
			return;
		}

		// remove trailing slash from url
		url = url.replace(/\/$/, '');

		const connection = {
			url,
			key,
			config: {
				enable: enable,
				prefix_id: prefixId,
				model_ids: modelIds
			}
		};

		await onSubmit(connection);

		loading = false;
		show = false;

		url = '';
		key = '';
		prefixId = '';
		modelIds = [];
	};

	const init = () => {
		if (connection) {
			url = connection.url;
			key = connection.key;

			enable = connection.config?.enable ?? true;
			prefixId = connection.config?.prefix_id ?? '';
			modelIds = connection.config?.model_ids ?? [];
		}
	};

	$effect(() => {
		if (show) {
			init();
		}
	});

	onMount(() => {
		init();
	});
</script>

<Modal size="sm" bind:show>
	<div>
		<div class=" flex justify-between text-content px-5 pt-4 pb-2">
			<div class=" text-lg font-medium self-center font-primary">
				{#if edit}
					{$i18n.t('Edit Connection')}
				{:else}
					{$i18n.t('Add Connection')}
				{/if}
			</div>
			<button
				class="self-center"
				type="button"
				aria-label={$i18n.t('Close')}
				onclick={() => {
					show = false;
				}}
			>
				<span class="i-lucide-x size-5 block"></span>
			</button>
		</div>

		<div class="flex flex-col w-full px-5 pb-4 text-content">
			<form
				class="flex flex-col w-full"
				onsubmit={(e) => {
					e.preventDefault();
					submitHandler();
				}}
			>
				<div class="flex gap-2 items-end">
					<div class="flex flex-col flex-1 min-w-0">
						<div class=" mb-1 text-xs text-content-muted">{$i18n.t('URL')}</div>

						<input
							class="w-full text-sm px-2 py-1 bg-surface-muted rounded-xs outline-hidden"
							type="text"
							bind:value={url}
							placeholder={$i18n.t('API Base URL')}
							autocomplete="off"
							required
						/>
					</div>

					<Tooltip content={$i18n.t('Verify Connection')} className="shrink-0">
						<button
							class="p-1.5 bg-transparent hover:bg-surface-hover rounded-lg"
							aria-label={$i18n.t('Verify Connection')}
							onclick={() => {
								verifyHandler();
							}}
							type="button"
						>
							<span class="i-lucide-refresh-cw size-4 block"></span>
						</button>
					</Tooltip>

					<Tooltip
						content={enable ? $i18n.t('Enabled') : $i18n.t('Disabled')}
						className="shrink-0 flex items-center h-7"
					>
						<Switch bind:state={enable} />
					</Tooltip>
				</div>

				<div class="flex gap-2 mt-2">
					<div class="flex flex-col w-full min-w-0">
						<div class=" mb-1 text-xs text-content-muted">{$i18n.t('Key')}</div>

						<SensitiveInput
							outerClassName="flex w-full px-2 bg-surface-muted rounded-xs"
							inputClassName="w-full text-sm py-1 placeholder:text-content-subtle bg-transparent outline-hidden"
							bind:value={key}
							placeholder={$i18n.t('API Key')}
							required={false}
						/>
					</div>

					<div class="flex flex-col w-full min-w-0">
						<div class=" mb-1 text-xs text-content-muted">{$i18n.t('Prefix ID')}</div>

						<Tooltip
							content={$i18n.t(
								'Prefix ID is used to avoid conflicts with other connections by adding a prefix to the model IDs - leave empty to disable'
							)}
						>
							<input
								class="w-full text-sm px-2 py-1 bg-surface-muted rounded-xs outline-hidden"
								type="text"
								bind:value={prefixId}
								placeholder={$i18n.t('Prefix ID')}
								autocomplete="off"
							/>
						</Tooltip>
					</div>
				</div>

				<hr class=" border-line my-2.5 w-full" />

				<div class="flex flex-col w-full">
					<div class="mb-1 flex justify-between">
						<div class="text-xs text-content-muted">{$i18n.t('Model IDs')}</div>
					</div>

					{#if modelIds.length > 0}
						<div class="flex flex-col">
							{#each modelIds as modelId, modelIdx}
								<div class=" flex gap-2 w-full justify-between items-center">
									<div class=" text-sm flex-1 py-1">
										{modelId}
									</div>
									<button
										class="shrink-0 p-1 hover:bg-surface-hover rounded-lg"
										type="button"
										aria-label={$i18n.t('Delete')}
										onclick={() => {
											modelIds = modelIds.filter((_, idx) => idx !== modelIdx);
										}}
									>
										<span class="i-lucide-minus size-3.5 block"></span>
									</button>
								</div>
							{/each}
						</div>
					{:else}
						<div class="text-content-subtle text-xs text-center py-2 px-10">
							{#if ollama}
								{$i18n.t('Leave empty to include all models from "{{url}}/api/tags" endpoint', {
									url: url
								})}
							{:else}
								{$i18n.t('Leave empty to include all models from "{{url}}/models" endpoint', {
									url: url
								})}
							{/if}
						</div>
					{/if}

					<div class="flex items-center gap-2 mt-1">
						<input
							class="flex-1 text-sm px-2 py-1 bg-surface-muted rounded-xs outline-hidden"
							bind:value={modelId}
							placeholder={$i18n.t('Add a model ID')}
						/>

						<button
							class="shrink-0 p-1 hover:bg-surface-hover rounded-lg"
							type="button"
							aria-label={$i18n.t('Add')}
							onclick={() => {
								addModelHandler();
							}}
						>
							<span class="i-lucide-plus size-3.5 block"></span>
						</button>
					</div>
				</div>

				<div class="flex justify-end pt-3 text-sm font-medium gap-1.5">
					{#if edit}
						<button
							class="px-3.5 py-1.5 text-sm font-medium bg-secondary hover:bg-secondary-hover text-secondary-content rounded-full"
							type="button"
							onclick={() => {
								onDelete();
								show = false;
							}}
						>
							{$i18n.t('Delete')}
						</button>
					{/if}

					<button
						class="px-3.5 py-1.5 text-sm font-medium bg-primary hover:bg-primary-hover text-primary-content rounded-full flex flex-row space-x-1 items-center {loading
							? ' cursor-not-allowed'
							: ''}"
						type="submit"
						disabled={loading}
					>
						{$i18n.t('Save')}

						{#if loading}
							<div class="ml-2 self-center">
								<Spinner className="size-4" />
							</div>
						{/if}
					</button>
				</div>
			</form>
		</div>
	</div>
</Modal>
