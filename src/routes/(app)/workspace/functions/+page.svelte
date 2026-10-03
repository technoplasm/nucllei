<script lang="ts">
	// llens(#48): Admin を Workspace に統合。admin ゲートは workspace layout 側。
	// llens(#45): share/clone/export/import・openwebui.com community・version/manifest 表示を撤去。
	// llens(#46): is_active / is_global 撤去 → 有効/global トグルも廃止。適用制御はワークスペースの
	// モデル別 filterIds / actionIds 割り当て一本。残すは 一覧/検索/作成/編集/削除 のみ(skill/tool 同型)。
	// llens(#47 A1): 100% 幅をやめて skill/tool と同じグリッド。クリックで編集、… メニューは trash に。
	import { toast } from 'svelte-sonner';
	import { onMount, getContext, tick } from 'svelte';

	import { WEBUI_NAME, functions, models } from '$lib/stores';
	import { deleteFunctionById, getFunctions } from '$lib/apis/functions';
	import { getModels } from '$lib/apis';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import DeleteConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import ValvesModal from '$lib/components/workspace/common/ValvesModal.svelte';

	const i18n = getContext('i18n');

	let query = $state('');

	let showValvesModal = $state(false);
	let selectedFunction = $state<any>(null);

	let showDeleteConfirm = $state(false);

	const filteredItems = $derived(
		(($functions ?? []) as any[])
			.filter(
				(f: any) =>
					query === '' ||
					f.name.toLowerCase().includes(query.toLowerCase()) ||
					f.id.toLowerCase().includes(query.toLowerCase())
			)
			.sort((a: any, b: any) => a.name.localeCompare(b.name))
	);

	// llens: badge をやめ、種別(Attachment / Filter / Action / Pipe)ごとにセクション分割して表示する
	// (ツール一覧の 組み込み/カスタム 分割と同じ様式)。未知の type は末尾に Other としてまとめる。
	const FUNCTION_TYPES = [
		{ type: 'attachment', label: 'Attachment' },
		{ type: 'filter', label: 'Filter' },
		{ type: 'action', label: 'Action' },
		{ type: 'pipe', label: 'Pipe' }
	];

	const groupedItems = $derived.by(() => {
		const known = new Set(FUNCTION_TYPES.map((t) => t.type));
		const groups = FUNCTION_TYPES.map((t) => ({
			label: t.label,
			items: filteredItems.filter((f: any) => f.type === t.type)
		}));
		const others = filteredItems.filter((f: any) => !known.has(f.type));
		if (others.length) groups.push({ label: 'Other', items: others });
		return groups.filter((g) => g.items.length);
	});

	const deleteHandler = async (func: any) => {
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

	onMount(async () => {
		functions.set(await getFunctions());
	});
</script>

<svelte:head>
	<title>
		{$i18n.t('Functions')} | {$WEBUI_NAME}
	</title>
</svelte:head>

{#if $functions !== null}
	<!-- ヘッダ様式: 見出しで 1 行(count なし)/ 2 行目に Create pill + 検索 -->
	<div class="flex flex-col gap-1 mt-1.5">
		<div class="flex items-center text-xl font-medium px-0.5">
			{$i18n.t('Functions')}
		</div>

		<div class="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2">
			<a
				class="shrink-0 whitespace-nowrap inline-flex items-center py-0.5 pl-2 pr-3 rounded-full bg-primary hover:bg-primary-hover text-primary-content font-medium text-sm"
				href="/workspace/functions/create"
			>
				<span class="i-lucide-plus size-3.5 block mr-1"></span>
				{$i18n.t('Create Function')}
			</a>

			<div class="flex items-center px-2 bg-surface-form rounded-xs">
				<span class="i-lucide-search size-3.5 block shrink-0"></span>
				<input
					class="w-full sm:w-48 text-sm px-2 py-1 bg-transparent outline-hidden"
					bind:value={query}
					placeholder={$i18n.t('Search Functions')}
				/>
			</div>
		</div>
	</div>

	<div class="mt-6">
		{#each groupedItems as group (group.label)}
			<div class="text-md font-medium text-content-muted px-0.5 mt-3 first:mt-0 mb-1">
				{$i18n.t(group.label)}
			</div>
			<div class="mb-3 gap-3 grid lg:grid-cols-2 xl:grid-cols-3">
				{#each group.items as func (func.id)}
					<div
						class="relative flex items-center gap-0.5 px-4 py-3 bg-surface-raised [&:hover:not(:has(button:hover))]:bg-surface-hover rounded-xl"
					>
						<a
							class="flex-1 min-w-0 cursor-pointer after:absolute after:inset-0 after:content-['']"
							href={`/workspace/functions/edit?id=${encodeURIComponent(func.id)}`}
						>
							<div class="font-semibold line-clamp-1">{func.name}</div>
							<div class="flex items-center gap-1.5 text-xs">
								<span
									class="font-mono text-[11px] leading-5 px-1.5 rounded-md bg-surface-muted text-content-muted shrink-0"
									>{func.id}</span
								>
								<span class="text-content-muted line-clamp-1">{func.description}</span>
							</div>
						</a>

						<Tooltip
							className="relative flex"
							content={func.has_valves ? $i18n.t('Values') : $i18n.t('No values')}
						>
							<button
								class="px-2 py-2 text-content bg-transparent hover:bg-surface-hover rounded-xl disabled:opacity-35 disabled:hover:bg-transparent"
								type="button"
								aria-label={$i18n.t('Values')}
								disabled={!func.has_valves}
								onclick={() => {
									selectedFunction = func;
									showValvesModal = true;
								}}
							>
								<span class="i-lucide-settings size-4 block"></span>
							</button>
						</Tooltip>

						<Tooltip className="relative flex" content={$i18n.t('Delete')}>
							<button
								class="px-2 py-2 text-content bg-transparent hover:bg-surface-hover rounded-xl"
								type="button"
								aria-label={$i18n.t('Delete')}
								onclick={() => {
									selectedFunction = func;
									showDeleteConfirm = true;
								}}
							>
								<span class="i-lucide-trash-2 size-4 block"></span>
							</button>
						</Tooltip>
					</div>
				{/each}
			</div>
		{/each}
	</div>

	<DeleteConfirmDialog
		bind:show={showDeleteConfirm}
		title={$i18n.t('Delete function?')}
		onConfirm={() => {
			deleteHandler(selectedFunction);
		}}
	>
		<div class=" text-sm text-content-muted">
			{$i18n.t('This will delete')} <span class=" font-semibold">{selectedFunction.name}</span>.
		</div>
	</DeleteConfirmDialog>

	<ValvesModal
		bind:show={showValvesModal}
		type="function"
		id={selectedFunction?.id ?? null}
		onSave={async () => {
			await tick();
			models.set(await getModels());
		}}
	/>
{/if}
