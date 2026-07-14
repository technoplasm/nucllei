<script lang="ts">
	import { toast } from 'svelte-sonner';

	import { WEBUI_NAME, config, functions, models, settings } from '$lib/stores';
	import { getContext, tick } from 'svelte';

	import { deleteFunctionById, getFunctions } from '$lib/apis/functions';

	import Tooltip from '../common/Tooltip.svelte';
	import { getModels } from '$lib/apis';
	import ValvesModal from '../workspace/common/ValvesModal.svelte';
	import DeleteConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import GarbageBin from '../icons/GarbageBin.svelte';
	import Search from '../icons/Search.svelte';
	import Plus from '../icons/Plus.svelte';

	// llens(#45): share/clone/export/import・openwebui.com community・version/manifest 表示を撤去。
	// llens(#46): is_active / is_global 撤去 → 有効/global トグルも廃止。適用制御はワークスペースの
	// モデル別 filterIds / actionIds 割り当て一本。残すは 一覧/検索/作成/編集/削除 のみ(skill/tool 同型)。
	// llens(#47 A1): 100% 幅をやめて skill/tool と同じグリッド。クリックで編集、… メニューは trash に。
	const i18n = getContext('i18n');

	let query = '';

	let showValvesModal = false;
	let selectedFunction = null;

	let showDeleteConfirm = false;

	let filteredItems = [];
	$: filteredItems = $functions
		.filter(
			(f) =>
				query === '' ||
				f.name.toLowerCase().includes(query.toLowerCase()) ||
				f.id.toLowerCase().includes(query.toLowerCase())
		)
		.sort((a, b) => a.name.localeCompare(b.name));

	// llens: badge をやめ、種別(Attachment / Filter / Action / Pipe)ごとにセクション分割して表示する
	// (ツール一覧の 組み込み/カスタム 分割と同じ様式)。未知の type は末尾に Other としてまとめる。
	const FUNCTION_TYPES = [
		{ type: 'attachment', label: 'Attachment' },
		{ type: 'filter', label: 'Filter' },
		{ type: 'action', label: 'Action' },
		{ type: 'pipe', label: 'Pipe' }
	];
	let groupedItems: any[] = [];
	$: {
		const known = new Set(FUNCTION_TYPES.map((t) => t.type));
		const groups = FUNCTION_TYPES.map((t) => ({
			label: t.label,
			items: filteredItems.filter((f: any) => f.type === t.type)
		}));
		const others = filteredItems.filter((f: any) => !known.has(f.type));
		if (others.length) groups.push({ label: 'Other', items: others });
		groupedItems = groups.filter((g) => g.items.length);
	}

	const deleteHandler = async (func) => {
		const res = await deleteFunctionById(func.id).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			toast.success($i18n.t('Function deleted successfully'));

			functions.set(await getFunctions());
			models.set(await getModels());
		}
	};
</script>

<svelte:head>
	<title>
		{$i18n.t('Functions')} | {$WEBUI_NAME}
	</title>
</svelte:head>

<div class="flex flex-col gap-1 mt-1.5 mb-2">
	<div class="flex justify-between items-center">
		<div class="flex md:self-center text-xl items-center font-medium px-0.5">
			{$i18n.t('Functions')}
			<div class="flex self-center w-[1px] h-6 mx-2.5 bg-gray-50 dark:bg-gray-850" />
			<span class="text-base font-lg text-content-muted">{filteredItems.length}</span>
		</div>
	</div>

	<div class=" flex w-full space-x-2">
		<div class="flex flex-1">
			<div class=" self-center ml-1 mr-3">
				<Search className="size-3.5" />
			</div>
			<input
				class=" w-full text-sm pr-4 py-1 rounded-r-xl outline-hidden bg-transparent"
				bind:value={query}
				placeholder={$i18n.t('Search Functions')}
			/>
		</div>

		<div>
			<a
				class=" px-2 py-2 rounded-xl hover:bg-gray-700/10 dark:hover:bg-gray-100/10 text-content dark:hover:text-white transition font-medium text-sm flex items-center space-x-1"
				href="/workspace/functions/create"
			>
				<Plus className="size-3.5" />
			</a>
		</div>
	</div>
</div>

{#each groupedItems as group (group.label)}
	<div class="text-xs font-medium text-content-muted px-0.5 mt-1 mb-1">
		{group.label}
	</div>
	<div class="mb-3 gap-2 grid lg:grid-cols-2 xl:grid-cols-3">
		{#each group.items as func (func.id)}
			<div
				class=" flex space-x-4 cursor-pointer w-full px-3 py-2 dark:hover:bg-white/5 hover:bg-black/5 rounded-xl"
			>
				<a
					class=" flex flex-1 space-x-3.5 cursor-pointer w-full min-w-0"
					href={`/workspace/functions/edit?id=${encodeURIComponent(func.id)}`}
				>
					<div class="flex items-center text-left min-w-0">
						<div class=" flex-1 self-center pl-1 min-w-0">
							<div class=" font-semibold flex items-center gap-1.5 line-clamp-1">
								{func.name}
							</div>

							<div class="flex gap-1.5 px-1">
								<div class=" text-content-muted text-xs font-medium shrink-0">{func.id}</div>

								<div class=" text-xs overflow-hidden text-ellipsis line-clamp-1">
									{func.description}
								</div>
							</div>
						</div>
					</div>
				</a>
				<div class="flex flex-row gap-0.5 self-center">
					<Tooltip content={$i18n.t('Valves')}>
					<button
						class="self-center w-fit text-sm px-2 py-2 text-content dark:hover:text-white hover:bg-black/5 dark:hover:bg-white/5 rounded-xl"
						type="button"
						on:click={() => {
							selectedFunction = func;
							showValvesModal = true;
						}}
					>
						<svg
							xmlns="http://www.w3.org/2000/svg"
							fill="none"
							viewBox="0 0 24 24"
							stroke-width="1.5"
							stroke="currentColor"
							class="size-4"
						>
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 0 1 1.37.49l1.296 2.247a1.125 1.125 0 0 1-.26 1.431l-1.003.827c-.293.241-.438.613-.43.992a7.723 7.723 0 0 1 0 .255c-.008.378.137.75.43.991l1.004.827c.424.35.534.955.26 1.43l-1.298 2.247a1.125 1.125 0 0 1-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.47 6.47 0 0 1-.22.128c-.331.183-.581.495-.644.869l-.213 1.281c-.09.543-.56.94-1.11.94h-2.594c-.55 0-1.019-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 0 1-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 0 1-1.369-.49l-1.297-2.247a1.125 1.125 0 0 1 .26-1.431l1.004-.827c.292-.24.437-.613.43-.991a6.932 6.932 0 0 1 0-.255c.007-.38-.138-.751-.43-.992l-1.004-.827a1.125 1.125 0 0 1-.26-1.43l1.297-2.247a1.125 1.125 0 0 1 1.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.086.22-.128.332-.183.582-.495.644-.869l.214-1.28Z"
							/>
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z"
							/>
						</svg>
					</button>
				</Tooltip>

				<Tooltip content={$i18n.t('Delete')}>
					<button
						class="self-center w-fit text-sm px-2 py-2 text-content dark:hover:text-white hover:bg-black/5 dark:hover:bg-white/5 rounded-xl"
						type="button"
						on:click={() => {
							selectedFunction = func;
							showDeleteConfirm = true;
						}}
					>
						<GarbageBin />
					</button>
				</Tooltip>
			</div>
		</div>
	{/each}
</div>
{/each}

<DeleteConfirmDialog
	bind:show={showDeleteConfirm}
	title={$i18n.t('Delete function?')}
	on:confirm={() => {
		deleteHandler(selectedFunction);
	}}
>
	<div class=" text-sm text-content-muted">
		{$i18n.t('This will delete')} <span class="  font-semibold">{selectedFunction.name}</span>.
	</div>
</DeleteConfirmDialog>

<ValvesModal
	bind:show={showValvesModal}
	type="function"
	id={selectedFunction?.id ?? null}
	on:save={async () => {
		await tick();
		models.set(await getModels());
	}}
/>
