<script lang="ts">
	import { toast } from 'svelte-sonner';

	import { onMount, getContext } from 'svelte';
	import { WEBUI_NAME, tools as _tools, user } from '$lib/stores';

	import { deleteToolById, getToolList, getTools, updateToolById } from '$lib/apis/tools';
	import Tooltip from '../common/Tooltip.svelte';
	import DeleteConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import GarbageBin from '../icons/GarbageBin.svelte';
	import Search from '../icons/Search.svelte';
	import Plus from '../icons/Plus.svelte';
	import Spinner from '../common/Spinner.svelte';
	import GlobeAlt from '../icons/GlobeAlt.svelte';
	import LockClosed from '../icons/LockClosed.svelte';
	import AccessControlModal from './common/AccessControlModal.svelte';

	// air-gapped: openwebui.com/community 連携(share)・可搬性導線(clone・import/export JSON)は
	// skill(#35)の前例どおり撤去した(issue #44)。機能は一覧/検索/作成/編集/削除のみ。
	// version/funding 等の frontmatter 由来メタ表示も frontmatter 廃止に伴い撤去。
	// llens(#47 A1): TOOL バッジ・name tooltip・By 表示・Valves・… メニューを撤去。
	// 組み込みは上部に別セクション。visibility(access_control)は一覧のアイコンから直接編集する
	// (builtin は access_control のみ更新可 — routers/tools.py)。
	const i18n = getContext('i18n');

	let loaded = false;

	let query = '';

	let selectedTool = null;

	let showDeleteConfirm = false;

	// visibility(access_control)の一覧内編集: アイコン → AccessControlModal、閉じたら保存
	let showAccessControlModal = false;
	let acItem = null;
	let acValue = null;

	let tools = [];
	let filteredItems = [];

	$: filteredItems = tools.filter(
		(t) =>
			query === '' ||
			t.name.toLowerCase().includes(query.toLowerCase()) ||
			t.id.toLowerCase().includes(query.toLowerCase())
	);

	$: builtinItems = filteredItems.filter((t) => t?.meta?.builtin);
	$: customItems = filteredItems.filter((t) => !t?.meta?.builtin);

	// llens(#47 A1): 一覧の2段目は description ではなく、その tool が公開する関数名を
	// できるだけ並べる(#49 の tool spec 露出。list は specs 込みの ToolUserModel)。
	const toolFnNames = (tool) => (tool?.specs ?? []).map((s) => s?.name).filter(Boolean).join(', ');

	const openAccessControl = (tool) => {
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

		const res = await updateToolById(localStorage.token, item.id, {
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

	$: if (!showAccessControlModal && acItem) {
		commitAccessControl();
	}

	const deleteHandler = async (tool) => {
		const res = await deleteToolById(localStorage.token, tool.id).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			toast.success($i18n.t('Tool deleted successfully'));
			await init();
		}
	};

	const init = async () => {
		tools = await getToolList(localStorage.token);
		_tools.set(await getTools(localStorage.token));
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
	<div class="flex flex-col gap-1 my-1.5">
		<div class="flex justify-between items-center">
			<div class="flex md:self-center text-xl font-medium px-0.5 items-center">
				{$i18n.t('Tools')}
				<div class="flex self-center w-[1px] h-6 mx-2.5 bg-gray-50 dark:bg-gray-850" />
				<span class="text-lg font-medium text-gray-500 dark:text-gray-300"
					>{filteredItems.length}</span
				>
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
					placeholder={$i18n.t('Search Tools')}
				/>
			</div>

			<div>
				<a
					class=" px-2 py-2 rounded-xl hover:bg-gray-700/10 dark:hover:bg-gray-100/10 dark:text-gray-300 dark:hover:text-white transition font-medium text-sm flex items-center space-x-1"
					href="/workspace/tools/create"
				>
					<Plus className="size-3.5" />
				</a>
			</div>
		</div>
	</div>

	<!-- llens(#47 A1): 組み込みツールは上部に別セクション。編集/削除は不可だが
	     visibility(access_control)だけは一覧のアイコンから直接編集できる(個別に開かせない)。 -->
	{#if builtinItems.length}
		<div class="text-xs font-medium text-gray-500 dark:text-gray-400 px-0.5 mt-1 mb-1">
			{$i18n.t('組み込み')}
		</div>
		<div class="mb-3 gap-2 grid lg:grid-cols-2 xl:grid-cols-3">
			{#each builtinItems as tool}
				<div
					class=" flex space-x-4 w-full px-3 py-2 dark:hover:bg-white/5 hover:bg-black/5 rounded-xl transition"
				>
					<div class=" flex flex-1 space-x-3.5 w-full min-w-0">
						<div class="flex items-center text-left min-w-0">
							<div class=" flex-1 self-center min-w-0">
								<div class=" font-semibold flex items-center gap-1.5 line-clamp-1">
									{tool.name}
									<span class=" text-gray-500 text-xs font-medium shrink-0">{tool.id}</span>
								</div>
								<div
									class="px-0.5 text-xs text-gray-500 overflow-hidden text-ellipsis line-clamp-1 mt-0.5"
								>
									{toolFnNames(tool)}
								</div>
							</div>
						</div>
					</div>

					<div class="flex flex-row gap-0.5 self-center">
						<Tooltip
							content={(tool.access_control ?? null) === null
								? $i18n.t('Public')
								: $i18n.t('Private')}
						>
							<button
								class="self-center w-fit text-sm px-2 py-2 dark:text-gray-300 dark:hover:text-white hover:bg-black/5 dark:hover:bg-white/5 rounded-xl"
								type="button"
								on:click={() => openAccessControl(tool)}
							>
								{#if (tool.access_control ?? null) === null}
									<GlobeAlt className="size-4" />
								{:else}
									<LockClosed className="size-4" />
								{/if}
							</button>
						</Tooltip>
					</div>
				</div>
			{/each}
		</div>
	{/if}

	<!-- 組み込みヘッダを出すときは、対称にカスタム側にもヘッダを付ける(組み込みが無ければ無ヘッダ) -->
	{#if builtinItems.length}
		<div class="text-xs font-medium text-gray-500 dark:text-gray-400 px-0.5 mt-1 mb-1">
			{$i18n.t('カスタム')}
		</div>
	{/if}
	<div class="mb-5 gap-2 grid lg:grid-cols-2 xl:grid-cols-3">
		{#each customItems as tool}
			<div
				class=" flex space-x-4 w-full px-3 py-2 cursor-pointer dark:hover:bg-white/5 hover:bg-black/5 rounded-xl transition"
			>
				<a
					class=" flex flex-1 space-x-3.5 w-full min-w-0 cursor-pointer"
					href={`/workspace/tools/edit?id=${encodeURIComponent(tool.id)}`}
				>
					<div class="flex items-center text-left min-w-0">
						<div class=" flex-1 self-center min-w-0">
							<div class=" font-semibold flex items-center gap-1.5 line-clamp-1">
								{tool.name}
								<span class=" text-gray-500 text-xs font-medium shrink-0">{tool.id}</span>
							</div>
							<div
								class="px-0.5 text-xs text-gray-500 overflow-hidden text-ellipsis line-clamp-1 mt-0.5"
							>
								{toolFnNames(tool)}
							</div>
						</div>
					</div>
				</a>

				<div class="flex flex-row gap-0.5 self-center">
					<Tooltip
						content={(tool.access_control ?? null) === null
							? $i18n.t('Public')
							: $i18n.t('Private')}
					>
						<button
							class="self-center w-fit text-sm px-2 py-2 dark:text-gray-300 dark:hover:text-white hover:bg-black/5 dark:hover:bg-white/5 rounded-xl"
							type="button"
							on:click={() => openAccessControl(tool)}
						>
							{#if (tool.access_control ?? null) === null}
								<GlobeAlt className="size-4" />
							{:else}
								<LockClosed className="size-4" />
							{/if}
						</button>
					</Tooltip>

					<Tooltip content={$i18n.t('Delete')}>
						<button
							class="self-center w-fit text-sm px-2 py-2 dark:text-gray-300 dark:hover:text-white hover:bg-black/5 dark:hover:bg-white/5 rounded-xl"
							type="button"
							on:click={() => {
								selectedTool = tool;
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

	<DeleteConfirmDialog
		bind:show={showDeleteConfirm}
		title={$i18n.t('Delete tool?')}
		on:confirm={() => {
			deleteHandler(selectedTool);
		}}
	>
		<div class=" text-sm text-gray-500">
			{$i18n.t('This will delete')} <span class="  font-semibold">{selectedTool.name}</span>.
		</div>
	</DeleteConfirmDialog>

	<AccessControlModal
		bind:show={showAccessControlModal}
		bind:accessControl={acValue}
		accessRoles={['read', 'write']}
		allowPublic={$user?.permissions?.sharing?.public_tools || $user?.role === 'admin'}
	/>
{:else}
	<div class="w-full h-full flex justify-center items-center">
		<Spinner />
	</div>
{/if}
