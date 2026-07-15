<script lang="ts">
	// llens(#70): Settings > Models(生モデル管理)から workspace/models へ移設。
	// モデルの表示順(MODEL_ORDER_LIST — picker /api/models のソートに適用)と
	// 既定モデル(DEFAULT_MODELS)をここで管理する。
	import { toast } from 'svelte-sonner';
	import { getContext } from 'svelte';

	import { models } from '$lib/stores';
	import { getModelsConfig, setModelsConfig } from '$lib/apis/configs';
	import { deleteAllModels } from '$lib/apis/models';

	import Modal from '$lib/components/common/Modal.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import ModelList from './ModelList.svelte';

	const i18n = getContext('i18n');

	let {
		show = $bindable(false),
		onSave = () => {}
	}: {
		show?: boolean;
		onSave?: () => void | Promise<void>;
	} = $props();

	let config = $state<any>(null);

	let selectedModelId = $state('');
	let defaultModelIds = $state<string[]>([]);
	let modelIds = $state<string[]>([]);

	let sortDirection = $state<'' | 'asc' | 'desc'>('');

	let loading = $state(false);
	let showResetModal = $state(false);

	const init = async () => {
		config = await getModelsConfig();

		defaultModelIds = (config?.DEFAULT_MODELS ?? '').split(',').filter((id: string) => id);

		const modelOrderList = config.MODEL_ORDER_LIST || [];
		const allModelIds = ($models ?? []).map((model) => model.id);
		const orderedSet = new Set(modelOrderList);

		modelIds = [
			// MODEL_ORDER_LIST の順(実在する id のみ)
			...modelOrderList.filter((id: string) => allModelIds.includes(id)),
			// リスト外(新規追加分など)は末尾に名前順で
			...allModelIds.filter((id: string) => !orderedSet.has(id)).sort((a, b) => a.localeCompare(b))
		];

		sortDirection = '';
	};

	$effect(() => {
		if (show) {
			init();
		}
	});

	const sortByName = () => {
		sortDirection = sortDirection === 'asc' ? 'desc' : 'asc';
		const name = (id: string) => ($models ?? []).find((model) => model.id === id)?.name ?? id;
		modelIds = [...modelIds].sort((a, b) =>
			sortDirection === 'asc' ? name(a).localeCompare(name(b)) : name(b).localeCompare(name(a))
		);
	};

	const addDefaultModel = (id: string) => {
		if (id && !defaultModelIds.includes(id)) {
			defaultModelIds = [...defaultModelIds, id];
		}
		selectedModelId = '';
	};

	const submitHandler = async () => {
		loading = true;

		const res = await setModelsConfig({
			DEFAULT_MODELS: defaultModelIds.join(','),
			MODEL_ORDER_LIST: modelIds
		}).catch(() => null);

		if (res) {
			toast.success($i18n.t('Models configuration saved successfully'));
			await onSave();
			show = false;
		} else {
			toast.error($i18n.t('Failed to save models configuration'));
		}

		loading = false;
	};
</script>

<ConfirmDialog
	title={$i18n.t('Reset All Models')}
	message={$i18n.t('This will delete all models including custom models and cannot be undone.')}
	bind:show={showResetModal}
	onConfirm={async () => {
		const res = await deleteAllModels().catch(() => null);
		if (res) {
			toast.success($i18n.t('All models deleted successfully'));
			await onSave();
			show = false;
		}
	}}
/>

<Modal size="sm" bind:show>
	<div>
		<div class=" flex justify-between text-content px-5 pt-3 pb-1">
			<div class=" text-lg font-medium self-center font-primary">
				{$i18n.t('Configure Models')}
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

		<div class="w-full px-5 pb-4 text-content">
			{#if config}
				<form
					class="flex flex-col w-full"
					onsubmit={(e) => {
						e.preventDefault();
						submitHandler();
					}}
				>
					<!-- 表示順(drag)。ラベルクリックで名前順ソート(昇順/降順トグル) -->
					<div class="flex flex-col w-full">
						<button class="mb-1 flex gap-2 self-start" type="button" onclick={sortByName}>
							<div class="text-xs text-content-muted">{$i18n.t('Reorder Models')}</div>

							{#if sortDirection !== ''}
								<span class="self-center">
									{#if sortDirection === 'asc'}
										<span class="i-lucide-chevron-up size-3 block"></span>
									{:else}
										<span class="i-lucide-chevron-down size-3 block"></span>
									{/if}
								</span>
							{/if}
						</button>

						<ModelList bind:modelIds />
					</div>

					<hr class=" border-line my-2.5 w-full" />

					<div class="flex flex-col w-full">
						<div class="mb-1 text-xs text-content-muted">{$i18n.t('Default Models')}</div>

						<select
							class="w-full px-2 py-1 text-sm rounded-lg bg-surface-muted {selectedModelId
								? ''
								: 'text-content-subtle'} outline-hidden"
							bind:value={selectedModelId}
							onchange={(e) => {
								addDefaultModel(e.currentTarget.value);
							}}
						>
							<option value="">{$i18n.t('Select a model')}</option>
							{#each ($models ?? []).filter((m) => !defaultModelIds.includes(m.id)) as model (model.id)}
								<option value={model.id} class="text-content">{model.name}</option>
							{/each}
						</select>

						{#if defaultModelIds.length > 0}
							<div class="flex flex-col mt-1">
								{#each defaultModelIds as modelId, modelIdx (modelId)}
									<div class=" flex gap-2 w-full justify-between items-center">
										<div class=" text-sm flex-1 py-1 rounded-lg">
											{($models ?? []).find((model) => model.id === modelId)?.name ?? modelId}
										</div>
										<button
											class="shrink-0 text-content-muted hover:text-content"
											type="button"
											aria-label={$i18n.t('Remove')}
											onclick={() => {
												defaultModelIds = defaultModelIds.filter((_, idx) => idx !== modelIdx);
											}}
										>
											<span class="i-lucide-minus size-3.5 block"></span>
										</button>
									</div>
								{/each}
							</div>
						{:else}
							<div class="text-content-muted text-xs text-center py-2">
								{$i18n.t('No models selected')}
							</div>
						{/if}
					</div>

					<div class="flex justify-between pt-3 text-sm font-medium gap-1.5">
						<Tooltip content={$i18n.t('This will delete all models including custom models')}>
							<button
								class="px-3.5 py-1.5 text-sm font-medium bg-secondary hover:bg-secondary-hover text-red-600 dark:text-red-400 transition rounded-lg"
								type="button"
								onclick={() => {
									showResetModal = true;
								}}
							>
								{$i18n.t('Reset All Models')}
							</button>
						</Tooltip>

						<button
							class="px-3.5 py-1.5 text-sm font-medium bg-primary hover:bg-primary-hover text-primary-content transition rounded-lg flex items-center {loading
								? ' cursor-not-allowed opacity-50'
								: ''}"
							type="submit"
							disabled={loading}
						>
							{$i18n.t('Save')}

							{#if loading}
								<span class="i-lucide-loader-circle size-4 block animate-spin ml-1.5"></span>
							{/if}
						</button>
					</div>
				</form>
			{:else}
				<div class="flex justify-center py-4">
					<Spinner />
				</div>
			{/if}
		</div>
	</div>
</Modal>
