<script lang="ts">
	// llens(#82): 接続(OpenAI 互換 / Ollama)から取り込まれている生モデルの棚卸しビュー。
	// 生モデルは「概念的に普通のモデルとして使えない」— チャットで使うには workspace/models で
	// base に選んで preset を作る。旧 upstream の生モデル編集経路(名前/Tool/hidden の upsert =
	// 生モデル id に Models 行を作って露出させる)はここから全撤去した。
	// Ollama 実体の取得/削除(pull)だけは接続側の管理として残す(ManageModelsModal)。
	import { onMount, getContext } from 'svelte';
	const i18n = getContext('i18n');

	import { DEFAULT_MODEL_IMAGE_URL } from '$lib/constants';
	import { getModels } from '$lib/apis';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import ManageModelsModal from './Models/ManageModelsModal.svelte';

	let models = $state<any[] | null>(null);

	let showManageModal = $state(false);
	let searchValue = $state('');

	const filteredModels = $derived(
		(models ?? [])
			.filter(
				(m) =>
					searchValue === '' ||
					m.name.toLowerCase().includes(searchValue.toLowerCase()) ||
					m.id.toLowerCase().includes(searchValue.toLowerCase())
			)
			.sort((a, b) => a.name.localeCompare(b.name))
	);

	const init = async () => {
		// /api/models/base = 接続由来の生モデル一覧(admin ゲート)。Models 行の有無と無関係。
		models = ((await getModels(true).catch(() => null)) ?? []).filter(
			(m: any) => m?.pipeline?.type !== 'filter'
		);
	};

	onMount(() => {
		init();
	});
</script>

<ManageModelsModal bind:show={showManageModal} />

{#if models !== null}
	<div class="flex flex-col gap-1 mt-1.5 mb-2">
		<div class="flex items-center text-xl font-medium px-0.5">
			{$i18n.t('Models')}
		</div>

		<div class="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2">
			<div class="flex items-center px-2 bg-surface-form rounded-xs">
				<span class="i-lucide-search size-3.5 block shrink-0"></span>
				<input
					class="w-full sm:w-48 text-sm px-2 py-1 bg-transparent outline-hidden"
					bind:value={searchValue}
					placeholder={$i18n.t('Search Models')}
				/>
			</div>

			<div class="ml-auto flex items-center gap-0.5">
				<Tooltip content={$i18n.t('Manage Models')}>
					<button
						class="p-1.5 rounded-lg text-content-muted hover:bg-surface-hover"
						type="button"
						aria-label={$i18n.t('Manage Models')}
						onclick={() => {
							showManageModal = true;
						}}
					>
						<span class="i-lucide-download size-4 block"></span>
					</button>
				</Tooltip>
			</div>
		</div>
	</div>

	<div class=" my-2 mb-5" id="model-list">
		{#if filteredModels.length > 0}
			{#each filteredModels as model (model.id)}
				<div class=" flex items-center gap-3.5 w-full px-3 py-2 rounded-lg">
					<img
						src={model?.meta?.profile_image_url ?? DEFAULT_MODEL_IMAGE_URL}
						alt={model.name}
						class=" rounded-full size-8 shrink-0 object-cover"
					/>

					<div class="flex-1 min-w-0">
						<div class="font-semibold line-clamp-1">{model.name}</div>
						<div class="text-xs text-content-muted line-clamp-1">
							{model?.ollama?.digest ? `${model.id} (${model.ollama.digest})` : model.id}
						</div>
					</div>

					<span
						class="shrink-0 text-xs font-mono text-content-muted bg-surface-muted rounded-md px-1.5 py-0.5"
					>
						{model?.owned_by ?? 'openai'}
					</span>
				</div>
			{/each}
		{:else}
			<div class="flex flex-col items-center justify-center w-full h-20">
				<div class="text-content-subtle text-xs">
					{$i18n.t('No models found')}
				</div>
			</div>
		{/if}
	</div>
{:else}
	<div class=" h-full w-full flex justify-center items-center">
		<Spinner />
	</div>
{/if}
