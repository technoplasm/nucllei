<script lang="ts">
	// openwebui.com/community 連携(share)・可搬性導線(clone・import/export JSON)は
	// skill(#35)の前例どおり撤去した(issue #44)。機能は一覧/検索/作成/編集/削除のみ。
	// llens(#47 A1): TOOL バッジ・name tooltip・By 表示・Valves・… メニューを撤去。
	// 組み込みは上部に別セクション。visibility(access_control)は一覧のアイコンから直接編集する
	// (builtin は access_control のみ更新可 — routers/tools.py)。
	import { toast } from 'svelte-sonner';
	import { onMount, getContext } from 'svelte';
	import { WEBUI_NAME, tools as _tools } from '$lib/stores';

	import { deleteToolById, getToolList, getTools, updateToolById } from '$lib/apis/tools';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import DeleteConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import AccessControlModal from '$lib/components/workspace/common/AccessControlModal.svelte';

	const i18n = getContext('i18n');

	let loaded = $state(false);
	let query = $state('');

	let selectedTool = $state<any>(null);
	let showDeleteConfirm = $state(false);

	// visibility(access_control)の一覧内編集: アイコン → AccessControlModal、閉じたら保存
	let showAccessControlModal = $state(false);
	let acItem = $state<any>(null);
	let acValue = $state<any>(null);

	let tools = $state<any[]>([]);

	const filteredItems = $derived(
		tools.filter(
			(t) =>
				query === '' ||
				t.name.toLowerCase().includes(query.toLowerCase()) ||
				t.id.toLowerCase().includes(query.toLowerCase())
		)
	);

	const builtinItems = $derived(filteredItems.filter((t) => t?.meta?.builtin));
	const customItems = $derived(filteredItems.filter((t) => !t?.meta?.builtin));

	// llens(#47 A1): 一覧の2段目は description ではなく、その tool が公開する関数名を
	// できるだけ並べる(#49 の tool spec 露出。list は specs 込みの ToolUserModel)。
	const toolFnNames = (tool: any) =>
		(tool?.specs ?? [])
			.map((s: any) => s?.name)
			.filter(Boolean)
			.join(', ');

	const openAccessControl = (tool: any) => {
		acItem = tool;
		acValue = tool?.access_control ?? null;
		showAccessControlModal = true;
	};

	// list の item は content 込み(ToolUserModel)なのでそのまま round-trip できる。
	// builtin も同経路(backend が access_control のみ反映 — routers/tools.py)。
	const commitAccessControl = async () => {
		const item = acItem;
		const value = acValue;
		acItem = null;
		if (!item) return;

		const res = await updateToolById(item.id, {
			...item,
			access_control: value
		}).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			await init();
		}
	};

	$effect(() => {
		if (!showAccessControlModal && acItem) {
			commitAccessControl();
		}
	});

	const deleteHandler = async (tool: any) => {
		const res = await deleteToolById(tool.id).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			toast.success($i18n.t('Tool deleted successfully'));
			await init();
		}
	};

	const init = async () => {
		tools = await getToolList();
		_tools.set(await getTools());
	};

	onMount(async () => {
		await init();
		loaded = true;
	});
</script>

<svelte:head>
	<title>
		{$i18n.t('Tools')} | {$WEBUI_NAME}
	</title>
</svelte:head>

{#if loaded}
	<div class="flex flex-col gap-1 mt-1.5">
		<div class="flex items-center text-xl font-medium px-0.5">
			{$i18n.t('Tools')}
		</div>

		<!-- 行に items-center が無いと flex 既定の stretch で pill が検索ボックスの高さに伸びる -->
		<div class="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2">
			<a
				class="shrink-0 whitespace-nowrap inline-flex items-center py-0.5 pl-2 pr-3 rounded-full bg-primary hover:bg-primary-hover text-primary-content font-medium text-sm"
				href="/workspace/tools/create"
			>
				<span class="i-lucide-plus size-3.5 block mr-1"></span>
				{$i18n.t('Create Tool')}
			</a>

			<div class="flex items-center px-2 bg-surface-form rounded-xs">
				<span class="i-lucide-search size-3.5 block shrink-0"></span>
				<input
					class="w-full sm:w-48 text-sm px-2 py-1 bg-transparent outline-hidden"
					bind:value={query}
					placeholder={$i18n.t('Search Tools')}
				/>
			</div>
		</div>
	</div>

	<!-- llens(#47 A1): 組み込みツールは上部に別セクション。編集/削除は不可だが
	 visibility(access_control)だけは一覧のアイコンから直接編集できる(個別に開かせない)。 -->
	{#if builtinItems.length}
		<div class="text-md font-medium text-content-muted px-0.5 mt-6 mb-1">
			{$i18n.t('Builtin')}
		</div>
		<div class="mb-3 gap-3 grid lg:grid-cols-2 xl:grid-cols-3">
			{#each builtinItems as tool (tool.id)}
				<div class="flex items-center gap-0.5 px-4 py-3 bg-surface-raised rounded-xl">
					<!-- 組み込みは編集不可 — tooltip とカーソルで明示(visibility だけ右のアイコンで編集可) -->
					<Tooltip
						className="flex-1 min-w-0"
						content={$i18n.t('Builtin tools cannot be edited.')}
						placement="top-start"
					>
						<div class="cursor-not-allowed">
							<div class="font-semibold line-clamp-1">{tool.name}</div>
							<div class="flex gap-1.5 text-xs">
								<span class="text-content-muted font-medium shrink-0">{tool.id}</span>
								<span class="text-content-muted line-clamp-1">{toolFnNames(tool)}</span>
							</div>
						</div>
					</Tooltip>

					<Tooltip
						content={(tool.access_control ?? null) === null
							? $i18n.t('Public')
							: $i18n.t('Private')}
					>
						<button
							class="px-2 py-2 text-content bg-transparent hover:bg-surface-hover rounded-xl"
							type="button"
							aria-label={(tool.access_control ?? null) === null
								? $i18n.t('Public')
								: $i18n.t('Private')}
							onclick={() => openAccessControl(tool)}
						>
							{#if (tool.access_control ?? null) === null}
								<span class="i-lucide-globe size-4 block"></span>
							{:else}
								<span class="i-lucide-lock size-4 block"></span>
							{/if}
						</button>
					</Tooltip>
				</div>
			{/each}
		</div>
	{/if}

	<!-- 組み込みヘッダを出すときは、対称にカスタム側にもヘッダを付ける(組み込みが無ければ無ヘッダ) -->
	{#if builtinItems.length}
		<div class="text-md font-medium text-content-muted px-0.5 mt-3 mb-1">
			{$i18n.t('Custom')}
		</div>
	{/if}
	<div class="{builtinItems.length ? '' : 'mt-6 '}mb-3 gap-3 grid lg:grid-cols-2 xl:grid-cols-3">
		{#each customItems as tool (tool.id)}
			<div
				class="relative flex items-center gap-0.5 px-4 py-3 bg-surface-raised [&:hover:not(:has(button:hover))]:bg-surface-hover rounded-xl"
			>
				<a
					class="flex-1 min-w-0 cursor-pointer after:absolute after:inset-0 after:content-['']"
					href={`/workspace/tools/edit?id=${encodeURIComponent(tool.id)}`}
				>
					<div class="font-semibold line-clamp-1">{tool.name}</div>
					<div class="flex gap-1.5 text-xs">
						<span class="text-content-muted font-medium shrink-0">{tool.id}</span>
						<span class="text-content-muted line-clamp-1">{toolFnNames(tool)}</span>
					</div>
				</a>

				<Tooltip
					className="relative flex"
					content={(tool.access_control ?? null) === null ? $i18n.t('Public') : $i18n.t('Private')}
				>
					<button
						class="px-2 py-2 text-content bg-transparent hover:bg-surface-hover rounded-xl"
						type="button"
						aria-label={(tool.access_control ?? null) === null
							? $i18n.t('Public')
							: $i18n.t('Private')}
						onclick={() => openAccessControl(tool)}
					>
						{#if (tool.access_control ?? null) === null}
							<span class="i-lucide-globe size-4 block"></span>
						{:else}
							<span class="i-lucide-lock size-4 block"></span>
						{/if}
					</button>
				</Tooltip>

				<Tooltip className="relative flex" content={$i18n.t('Delete')}>
					<button
						class="px-2 py-2 text-content bg-transparent hover:bg-surface-hover rounded-xl"
						type="button"
						aria-label={$i18n.t('Delete')}
						onclick={() => {
							selectedTool = tool;
							showDeleteConfirm = true;
						}}
					>
						<span class="i-lucide-trash-2 size-4 block"></span>
					</button>
				</Tooltip>
			</div>
		{/each}
	</div>

	<DeleteConfirmDialog
		bind:show={showDeleteConfirm}
		title={$i18n.t('Delete tool?')}
		onConfirm={() => {
			deleteHandler(selectedTool);
		}}
	>
		<div class=" text-sm text-content-muted">
			{$i18n.t('This will delete')} <span class=" font-semibold">{selectedTool.name}</span>.
		</div>
	</DeleteConfirmDialog>

	<AccessControlModal
		bind:show={showAccessControlModal}
		bind:accessControl={acValue}
		allowPublic={true}
	/>
{:else}
	<div class="w-full h-full flex justify-center items-center">
		<Spinner />
	</div>
{/if}
