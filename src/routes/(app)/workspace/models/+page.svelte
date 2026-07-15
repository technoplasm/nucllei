<script lang="ts">
	// llens(#47 A1): By 撤去。#70 A2: … メニューを廃止し Clone/Delete を行内アイコンに
	// (skills/tools 同型)。可搬性導線のうち Import/Export JSON は skills(#35)/tools(#44)の
	// 前例どおり撤去、Clone(プリセット複製 = 編集の起点)のみ残す(2026-07-15 決定)。
	// shiftKey 即時削除(確認なし)も廃止 — 削除は常に confirm を挟む。
	import { toast } from 'svelte-sonner';
	import { onMount, getContext } from 'svelte';
	import { goto } from '$app/navigation';

	import { render } from '$lib/utils/markdown-it';
	import { DEFAULT_MODEL_IMAGE_URL } from '$lib/constants';
	import { WEBUI_NAME, models as _models } from '$lib/stores';
	import {
		deleteModelById,
		getModels as getWorkspaceModels,
		toggleModelById
	} from '$lib/apis/models';
	import { getModels } from '$lib/apis';

	import DeleteConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import ConfigureModelsModal from './ConfigureModelsModal.svelte';

	const i18n = getContext('i18n');

	let loaded = $state(false);
	let query = $state('');
	let models = $state<any[]>([]);

	let showDeleteConfirm = $state(false);
	let deleteModel = $state<any>(null);

	let showConfigModal = $state(false);

	const filteredItems = $derived(
		models.filter(
			(m) =>
				query === '' ||
				m.name.toLowerCase().includes(query.toLowerCase()) ||
				m.id.toLowerCase().includes(query.toLowerCase())
		)
	);

	const deleteHandler = async (model: any) => {
		const res = await deleteModelById(model.id).catch((e) => {
			toast.error(`${e}`);
			return null;
		});

		if (res) {
			toast.success($i18n.t(`Deleted {{name}}`, { name: model.id }));
		}

		await init();
	};

	const cloneHandler = (model: any) => {
		sessionStorage.model = JSON.stringify({
			...model,
			id: `${model.id}-clone`,
			name: `${model.name} (Clone)`
		});
		goto('/workspace/models/create');
	};

	const init = async () => {
		const list = await getWorkspaceModels();
		await _models.set(await getModels());

		// picker(/api/models — MODEL_ORDER_LIST 適用済み)と同じ順で一覧を並べる。
		// picker に出ないモデル(無効化中など)は末尾に名前順で。
		const order = new Map(($_models ?? []).map((m: any, i: number) => [m.id, i]));
		models = list.sort((a: any, b: any) => {
			const ai = order.get(a.id) ?? Infinity;
			const bi = order.get(b.id) ?? Infinity;
			if (ai !== bi) return ai - bi;
			return a.name.localeCompare(b.name);
		});
	};

	onMount(async () => {
		await init();
		loaded = true;
	});
</script>

<svelte:head>
	<title>
		{$i18n.t('Models')} | {$WEBUI_NAME}
	</title>
</svelte:head>

{#if loaded}
	<DeleteConfirmDialog
		bind:show={showDeleteConfirm}
		title={$i18n.t('Delete model?')}
		on:confirm={() => {
			deleteHandler(deleteModel);
		}}
	>
		<div class=" text-sm text-content-muted">
			{$i18n.t('This will delete')} <span class="  font-semibold">{deleteModel.id}</span>.
		</div>
	</DeleteConfirmDialog>

	<div class="flex flex-col gap-1 mt-1.5">
		<div class="flex items-center text-xl font-medium px-0.5">
			{$i18n.t('Models')}
			<div class="w-[1px] h-6 mx-2.5 bg-gray-50 dark:bg-gray-850"></div>
			<span class="text-base text-content-muted">{filteredItems.length}</span>
		</div>

		<!-- 狭幅は「Create ←端寄せ→ [↑↓] / 検索(全幅)」の 2 行に折り返す。
		     DOM は 1 セットのまま flex の order + wrap で並べ替える({#if} 二重化はしない)。 -->
		<div class="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2">
			<a
				class="order-1 shrink-0 whitespace-nowrap inline-flex items-center px-3 py-1 rounded-full bg-primary hover:bg-primary-hover text-primary-content transition font-medium text-sm"
				href="/workspace/models/create"
			>
				<span class="i-lucide-plus size-3.5 block mr-1"></span>
				{$i18n.t('Create Model')}
			</a>

			<div
				class="order-3 w-full sm:order-2 sm:w-auto flex items-center px-2 bg-surface-raised rounded-xs"
			>
				<span class="i-lucide-search size-3.5 block shrink-0"></span>
				<input
					class="w-full sm:w-48 text-sm px-2 py-1 bg-transparent outline-hidden"
					bind:value={query}
					placeholder={$i18n.t('Search Models')}
				/>
			</div>

			<Tooltip
				content={$i18n.t('Display order and default model')}
				className="order-2 ml-auto self-stretch flex sm:order-3 sm:ml-0"
			>
				<button
					class="h-full flex items-center px-2 py-1 bg-surface-raised hover:bg-surface-hover rounded-xs"
					type="button"
					aria-label={$i18n.t('Configure Models')}
					onclick={() => {
						showConfigModal = true;
					}}
				>
					<span class="i-lucide-arrow-up-down size-3.5 block"></span>
				</button>
			</Tooltip>
		</div>
	</div>

	<ConfigureModelsModal bind:show={showConfigModal} onSave={init} />

	<div class="mt-6 gap-3 grid lg:grid-cols-2 xl:grid-cols-3">
		{#each filteredItems as model (model.id)}
			<!-- hover は実際にクリックできる範囲(編集リンク)だけに付ける — アイコン類は各自の hover -->
			<div class="flex items-center gap-0.5 pr-3 bg-surface-raised rounded-xl overflow-hidden">
				<a
					class="flex items-center gap-3 flex-1 min-w-0 self-stretch px-3 py-2 cursor-pointer hover:bg-surface-hover"
					href={`/workspace/models/edit?id=${encodeURIComponent(model.id)}`}
				>
					<img
						src={model?.meta?.profile_image_url ?? DEFAULT_MODEL_IMAGE_URL}
						alt={model.name}
						class="rounded-full size-9 object-cover shrink-0 {model.is_active ? '' : 'opacity-50'}"
					/>

					<div class="flex-1 min-w-0 {model.is_active ? '' : 'text-content-muted'}">
						<Tooltip
							content={render(model?.meta?.description ?? model.id)}
							className=" w-fit"
							placement="top-start"
						>
							<div class="font-semibold line-clamp-1">{model.name}</div>
						</Tooltip>
						<div class="text-xs line-clamp-1">
							{#if (model?.meta?.description ?? '').trim()}
								{model?.meta?.description}
							{:else}
								{model.id}
							{/if}
						</div>
					</div>
				</a>

				<Tooltip content={$i18n.t('Clone')}>
					<button
						class="px-2 py-2 text-content dark:hover:text-white hover:bg-black/5 dark:hover:bg-white/5 rounded-xl"
						type="button"
						aria-label={$i18n.t('Clone')}
						onclick={() => {
							cloneHandler(model);
						}}
					>
						<span class="i-lucide-copy size-4 block"></span>
					</button>
				</Tooltip>

				<Tooltip content={$i18n.t('Delete')}>
					<button
						class="px-2 py-2 text-content dark:hover:text-white hover:bg-black/5 dark:hover:bg-white/5 rounded-xl"
						type="button"
						aria-label={$i18n.t('Delete')}
						onclick={() => {
							deleteModel = model;
							showDeleteConfirm = true;
						}}
					>
						<span class="i-lucide-trash-2 size-4 block"></span>
					</button>
				</Tooltip>

				<div class="ml-1">
					<Tooltip content={model.is_active ? $i18n.t('Enabled') : $i18n.t('Disabled')}>
						<Switch
							bind:state={model.is_active}
							on:change={async () => {
								await toggleModelById(model.id);
								await _models.set(await getModels());
							}}
						/>
					</Tooltip>
				</div>
			</div>
		{/each}
	</div>
{:else}
	<div class="w-full h-full flex justify-center items-center">
		<Spinner />
	</div>
{/if}
