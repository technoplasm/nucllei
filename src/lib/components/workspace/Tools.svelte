<script lang="ts">
	import { toast } from 'svelte-sonner';

	import { onMount, getContext } from 'svelte';
	import { WEBUI_NAME, tools as _tools } from '$lib/stores';

	import { goto } from '$app/navigation';
	import { deleteToolById, getToolList, getTools } from '$lib/apis/tools';
	import Tooltip from '../common/Tooltip.svelte';
	import ToolMenu from './Tools/ToolMenu.svelte';
	import EllipsisHorizontal from '../icons/EllipsisHorizontal.svelte';
	import ValvesModal from './common/ValvesModal.svelte';
	import DeleteConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import GarbageBin from '../icons/GarbageBin.svelte';
	import Search from '../icons/Search.svelte';
	import Plus from '../icons/Plus.svelte';
	import Spinner from '../common/Spinner.svelte';
	import { capitalizeFirstLetter } from '$lib/utils';

	// air-gapped: openwebui.com/community 連携(share)・可搬性導線(clone・import/export JSON)は
	// skill(#35)の前例どおり撤去した(issue #44)。機能は一覧/検索/作成/編集/削除のみ。
	// version/funding 等の frontmatter 由来メタ表示も frontmatter 廃止に伴い撤去。
	const i18n = getContext('i18n');

	let shiftKey = false;
	let loaded = false;

	let query = '';

	let showValvesModal = false;
	let selectedTool = null;

	let showDeleteConfirm = false;

	let tools = [];
	let filteredItems = [];

	$: filteredItems = tools.filter(
		(t) =>
			query === '' ||
			t.name.toLowerCase().includes(query.toLowerCase()) ||
			t.id.toLowerCase().includes(query.toLowerCase())
	);

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

		const onKeyDown = (event) => {
			if (event.key === 'Shift') {
				shiftKey = true;
			}
		};

		const onKeyUp = (event) => {
			if (event.key === 'Shift') {
				shiftKey = false;
			}
		};

		const onBlur = () => {
			shiftKey = false;
		};

		window.addEventListener('keydown', onKeyDown);
		window.addEventListener('keyup', onKeyUp);
		window.addEventListener('blur-sm', onBlur);

		return () => {
			window.removeEventListener('keydown', onKeyDown);
			window.removeEventListener('keyup', onKeyUp);
			window.removeEventListener('blur-sm', onBlur);
		};
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

	<div class="mb-5 gap-2 grid lg:grid-cols-2 xl:grid-cols-3">
		{#each filteredItems as tool}
			<div
				class=" flex space-x-4 w-full px-3 py-2 dark:hover:bg-white/5 hover:bg-black/5 rounded-xl transition {tool
					?.meta?.builtin
					? ''
					: 'cursor-pointer'}"
			>
				<!-- llens(#44): builtin は編集不可。編集リンクにせず素の行として描画する。 -->
				<a
					class=" flex flex-1 space-x-3.5 w-full {tool?.meta?.builtin ? '' : 'cursor-pointer'}"
					href={tool?.meta?.builtin
						? undefined
						: `/workspace/tools/edit?id=${encodeURIComponent(tool.id)}`}
				>
					<div class="flex items-center text-left">
						<div class=" flex-1 self-center">
							<Tooltip content={tool?.description ?? ''} placement="top-start">
								<div class=" font-semibold flex items-center gap-1.5">
									<div
										class=" text-xs font-bold px-1 rounded-sm uppercase line-clamp-1 bg-gray-500/20 text-gray-700 dark:text-gray-200"
									>
										TOOL
									</div>

									{#if tool?.meta?.builtin}
										<div
											class="text-xs font-bold px-1 rounded-sm line-clamp-1 bg-blue-500/20 text-blue-700 dark:text-blue-200"
										>
											{$i18n.t('組み込み')}
										</div>
									{/if}

									<div class="line-clamp-1">
										{tool.name}

										<span class=" text-gray-500 text-xs font-medium shrink-0">{tool.id}</span>
									</div>
								</div>
							</Tooltip>

							<div class="px-0.5">
								<div class="flex gap-1.5 mt-0.5 mb-0.5">
									<div class=" text-xs overflow-hidden text-ellipsis line-clamp-1">
										{tool.description}
									</div>
								</div>

								<div class="text-xs text-gray-500 shrink-0">
									<Tooltip
										content={tool?.user?.email ?? $i18n.t('Deleted User')}
										className="flex shrink-0"
										placement="top-start"
									>
										{$i18n.t('By {{name}}', {
											name: capitalizeFirstLetter(
												tool?.user?.name ?? tool?.user?.email ?? $i18n.t('Deleted User')
											)
										})}
									</Tooltip>
								</div>
							</div>
						</div>
					</div>
				</a>

				<!-- llens(#44): builtin は編集/複製/共有/エクスポート/削除・Valves を出さない(バッジのみ)。 -->
				{#if !tool?.meta?.builtin}
					<div class="flex flex-row gap-0.5 self-center">
						{#if shiftKey}
							<Tooltip content={$i18n.t('Delete')}>
								<button
									class="self-center w-fit text-sm px-2 py-2 dark:text-gray-300 dark:hover:text-white hover:bg-black/5 dark:hover:bg-white/5 rounded-xl"
									type="button"
									on:click={() => {
										deleteHandler(tool);
									}}
								>
									<GarbageBin />
								</button>
							</Tooltip>
						{:else}
							<Tooltip content={$i18n.t('Valves')}>
								<button
									class="self-center w-fit text-sm px-2 py-2 dark:text-gray-300 dark:hover:text-white hover:bg-black/5 dark:hover:bg-white/5 rounded-xl"
									type="button"
									on:click={() => {
										selectedTool = tool;
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

							<ToolMenu
								editHandler={() => {
									goto(`/workspace/tools/edit?id=${encodeURIComponent(tool.id)}`);
								}}
								deleteHandler={async () => {
									selectedTool = tool;
									showDeleteConfirm = true;
								}}
								onClose={() => {}}
							>
								<button
									class="self-center w-fit text-sm p-1.5 dark:text-gray-300 dark:hover:text-white hover:bg-black/5 dark:hover:bg-white/5 rounded-xl"
									type="button"
								>
									<EllipsisHorizontal className="size-5" />
								</button>
							</ToolMenu>
						{/if}
					</div>
				{/if}
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

	<ValvesModal bind:show={showValvesModal} type="tool" id={selectedTool?.id ?? null} />
{:else}
	<div class="w-full h-full flex justify-center items-center">
		<Spinner />
	</div>
{/if}
