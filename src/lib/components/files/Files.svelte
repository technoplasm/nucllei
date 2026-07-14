<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import dayjs from 'dayjs';

	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import { WEBUI_NAME, user } from '$lib/stores';
	import { formatFileSize } from '$lib/utils';

	import {
		getFileList,
		getFileUploaders,
		getFileById,
		uploadFile,
		deleteFileById,
		updateFileAccessControlById
	} from '$lib/apis/files';

	import DeleteConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import FileItemModal from '$lib/components/common/FileItemModal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import AccessControlModal from '$lib/components/workspace/common/AccessControlModal.svelte';

	import Search from '$lib/components/icons/Search.svelte';
	import Plus from '$lib/components/icons/Plus.svelte';
	import GarbageBin from '$lib/components/icons/GarbageBin.svelte';
	import ChevronUp from '$lib/components/icons/ChevronUp.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import Squares2x2 from '$lib/components/icons/Squares2x2.svelte';
	import ListBullet from '$lib/components/icons/ListBullet.svelte';
	import Document from '$lib/components/icons/Document.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';

	import FileVisibilityIcon from './FileVisibilityIcon.svelte';

	// llens(#69): ファイル管理ページ。一覧はサーバーページング(GET /files/list)。
	// 非 admin は常に自分の file のみ。admin は uploaders フィルタで他ユーザーを覗ける
	// (選択が空 = 自分の file のみ)。ACL 編集は Skills と同じ「アイコン → モーダル → 閉時 commit」。
	const i18n = getContext('i18n');

	let loaded = false;
	let loading = false;

	let files = [];
	let total = 0;

	let query = '';
	let queryDebounceTimeout: ReturnType<typeof setTimeout> | null = null;

	let viewMode: 'grid' | 'table' = 'grid';
	let orderBy: 'created_at' | 'accessed_at' = 'created_at';
	let direction: 'desc' | 'asc' = 'desc';
	let groupBy: 'day' | 'month' | 'year' = 'day';
	let limit = 60;

	// admin: uploaders フィルタ(chips)
	let uploaders = [];
	let selectedUploaderIds = [];
	let selectedUploaderId = '';

	$: isAdmin = $user?.role === 'admin';

	const fetchFiles = async (append = false) => {
		loading = true;

		const res = await getFileList({
			limit,
			offset: append ? files.length : 0,
			order_by: orderBy,
			direction,
			filename: query !== '' ? query : undefined,
			user_ids: isAdmin
				? selectedUploaderIds.length > 0
					? selectedUploaderIds
					: [$user?.id]
				: undefined
		}).catch((err) => {
			toast.error(`${err}`);
			return null;
		});

		if (res) {
			// append 中にサーバー側の並びが変わりうる(accessed_at ソート中のプレビュー等)。
			// 同一 id の重複は keyed each を壊すので除外する。
			files = append
				? [...files, ...res.items.filter((item) => !files.some((f) => f.id === item.id))]
				: res.items;
			total = res.total;
		}

		loading = false;
	};

	// 検索(filename 部分一致・サーバーフィルタ・debounce)
	const onQueryInput = () => {
		if (queryDebounceTimeout) {
			clearTimeout(queryDebounceTimeout);
		}
		queryDebounceTimeout = setTimeout(() => {
			fetchFiles(false);
		}, 300);
	};

	const setViewMode = (mode: 'grid' | 'table') => {
		viewMode = mode;
		localStorage.setItem('files-view-mode', mode);
	};

	// ソート(テーブルヘッダ / セレクタ共通)。キー変更時は desc から
	const setSortKey = (key: 'created_at' | 'accessed_at') => {
		if (orderBy === key) {
			direction = direction === 'desc' ? 'asc' : 'desc';
		} else {
			orderBy = key;
			direction = 'desc';
		}
		fetchFiles(false);
	};

	// 期間グルーピング(アクティブなソート時刻を基準に境界検出)
	const computeSections = (files, groupBy, orderBy) => {
		const labelOf = (file) => {
			const d = dayjs((file?.[orderBy] ?? 0) * 1000);
			if (groupBy === 'year') {
				return d.format('YYYY');
			} else if (groupBy === 'month') {
				return d.format('YYYY-MM');
			}
			return d.format('YYYY-MM-DD');
		};

		const sections = [];
		for (const file of files) {
			const label = labelOf(file);
			if (sections.length === 0 || sections[sections.length - 1].label !== label) {
				sections.push({ label, items: [file] });
			} else {
				sections[sections.length - 1].items.push(file);
			}
		}
		return sections;
	};

	$: sections = computeSections(files, groupBy, orderBy);

	const isImage = (file) => (file?.meta?.content_type ?? '').startsWith('image/');

	const visibilityLabel = (file) => {
		const ac = file?.access_control ?? null;
		if (ac === null) {
			return $i18n.t('Private');
		} else if (ac?.read?.public) {
			return $i18n.t('Public');
		}
		return $i18n.t('Shared');
	};

	const formatTimestamp = (ts) => dayjs((ts ?? 0) * 1000).format('YYYY-MM-DD HH:mm');

	// アップロード(複数可)
	let fileInput: HTMLInputElement | null = null;
	let uploading = false;

	const uploadHandler = async (fileList: File[]) => {
		if (fileList.length === 0) {
			return;
		}
		uploading = true;

		for (const file of fileList) {
			await uploadFile(file).catch((err) => {
				toast.error(`${file.name}: ${err}`);
				return null;
			});
		}

		uploading = false;
		await fetchFiles(false);
	};

	// visibility(access_control)の一覧内編集: アイコン → AccessControlModal、閉じたら保存
	let showAccessControlModal = false;
	let acItem = null;
	let acValue = null;

	const openAccessControl = (file) => {
		acItem = file;
		acValue = file?.access_control ?? null;
		showAccessControlModal = true;
	};

	const commitAccessControl = async () => {
		const item = acItem;
		const value = acValue;
		acItem = null;
		if (!item) return;

		const res = await updateFileAccessControlById(item.id, value).catch((err) => {
			toast.error(`${err}`);
			return null;
		});

		if (res) {
			files = files.map((f) =>
				f.id === item.id ? { ...f, access_control: res.access_control ?? null } : f
			);
			// 閲覧モーダルを開いたまま共有を変えた場合も表示を追従させる
			if (selectedItem?.id === item.id) {
				selectedItem = {
					...selectedItem,
					file: { ...selectedItem.file, access_control: res.access_control ?? null }
				};
			}
		}
	};

	$: if (!showAccessControlModal && acItem) {
		commitAccessControl();
	}

	// 閲覧モーダル(/files/list は content を含まないため、開く前に getFileById で data 込みを取得)
	let showFileModal = false;
	let selectedItem = null;

	const openFile = async (file) => {
		const res = await getFileById(file.id).catch((err) => {
			toast.error(`${err}`);
			return null;
		});
		if (!res) return;

		selectedItem = {
			id: res.id,
			name: res?.meta?.name ?? res?.filename,
			size: res?.meta?.size,
			file: res
		};
		showFileModal = true;
	};

	// 削除
	let showDeleteConfirm = false;
	let deleteTarget = null;

	const deleteHandler = async (file) => {
		const res = await deleteFileById(file.id).catch((err) => {
			toast.error(`${err}`);
			return null;
		});

		if (res) {
			files = files.filter((f) => f.id !== file.id);
			total = Math.max(0, total - 1);
		}
	};

	onMount(async () => {
		const storedViewMode = localStorage.getItem('files-view-mode');
		if (storedViewMode === 'grid' || storedViewMode === 'table') {
			viewMode = storedViewMode;
		}

		if (isAdmin) {
			uploaders = (await getFileUploaders().catch(() => null)) ?? [];
		}

		await fetchFiles(false);
		loaded = true;
	});
</script>

<svelte:head>
	<title>
		{$i18n.t('Files')} | {$WEBUI_NAME}
	</title>
</svelte:head>

{#if loaded}
	<DeleteConfirmDialog
		bind:show={showDeleteConfirm}
		title={$i18n.t('Delete file?')}
		on:confirm={() => {
			deleteHandler(deleteTarget);
		}}
	>
		<div class=" text-sm text-content-muted">
			{$i18n.t('This will delete')}
			<span class="  font-semibold">{deleteTarget?.meta?.name ?? deleteTarget?.filename}</span>.
		</div>
	</DeleteConfirmDialog>

	{#if selectedItem}
		<FileItemModal
			bind:show={showFileModal}
			item={selectedItem}
			download={true}
			onShare={() => {
				const file = files.find((f) => f.id === selectedItem.id) ?? selectedItem.file;
				openAccessControl(file);
			}}
			onDelete={() => {
				deleteTarget = selectedItem.file;
				showFileModal = false;
				showDeleteConfirm = true;
			}}
		/>
	{/if}

	<input
		bind:this={fileInput}
		type="file"
		multiple
		hidden
		on:change={async (e) => {
			await uploadHandler(Array.from(e.target?.files ?? []));
			if (fileInput) {
				fileInput.value = '';
			}
		}}
	/>

	<div class="flex flex-col gap-1 my-1.5">
		<div class="flex justify-between items-center">
			<div class="flex md:self-center text-xl font-medium px-0.5 items-center">
				{$i18n.t('Files')}
				<div class="flex self-center w-[1px] h-6 mx-2.5 bg-gray-50 dark:bg-gray-850" />
				<span class="text-lg font-medium text-content-muted">{total}</span>
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
					on:input={onQueryInput}
					placeholder={$i18n.t('Search Files')}
				/>
			</div>

			<div>
				<Tooltip content={$i18n.t('Upload Files')}>
					<button
						class=" px-2 py-2 rounded-xl hover:bg-gray-700/10 dark:hover:bg-gray-100/10 text-content-muted hover:text-content transition font-medium text-sm flex items-center space-x-1"
						type="button"
						disabled={uploading}
						on:click={() => {
							fileInput?.click();
						}}
					>
						{#if uploading}
							<Spinner className="size-3.5" />
						{:else}
							<Plus className="size-3.5" />
						{/if}
					</button>
				</Tooltip>
			</div>
		</div>

		<!-- 表示コントロール: 表示形式 / ソート / 期間グルーピング / 読み込み数 -->
		<div class="flex flex-wrap items-center gap-x-3 gap-y-1 px-0.5 text-xs text-content-muted">
			<div class="flex items-center gap-0.5">
				<Tooltip content={$i18n.t('Card View')}>
					<button
						class="p-1.5 rounded-lg transition {viewMode === 'grid'
							? 'bg-gray-100 dark:bg-gray-850 text-content'
							: 'hover:bg-black/5 dark:hover:bg-white/5'}"
						type="button"
						on:click={() => setViewMode('grid')}
					>
						<Squares2x2 className="size-3.5" />
					</button>
				</Tooltip>
				<Tooltip content={$i18n.t('Table View')}>
					<button
						class="p-1.5 rounded-lg transition {viewMode === 'table'
							? 'bg-gray-100 dark:bg-gray-850 text-content'
							: 'hover:bg-black/5 dark:hover:bg-white/5'}"
						type="button"
						on:click={() => setViewMode('table')}
					>
						<ListBullet className="size-3.5" />
					</button>
				</Tooltip>
			</div>

			<div class="flex items-center gap-0.5">
				<select
					class="dark:bg-gray-900 w-fit pr-6 rounded-sm text-xs bg-transparent outline-hidden"
					value={orderBy}
					on:change={(e) => {
						orderBy = e.target.value;
						fetchFiles(false);
					}}
				>
					<option value="created_at">{$i18n.t('Created at')}</option>
					<option value="accessed_at">{$i18n.t('Last Accessed')}</option>
				</select>

				<button
					class="p-1.5 rounded-lg hover:bg-black/5 dark:hover:bg-white/5 transition"
					type="button"
					on:click={() => {
						direction = direction === 'desc' ? 'asc' : 'desc';
						fetchFiles(false);
					}}
				>
					{#if direction === 'desc'}
						<ChevronDown className="size-3" />
					{:else}
						<ChevronUp className="size-3" />
					{/if}
				</button>
			</div>

			<div class="flex items-center gap-0.5">
				<select
					class="dark:bg-gray-900 w-fit pr-6 rounded-sm text-xs bg-transparent outline-hidden"
					bind:value={groupBy}
				>
					<option value="day">{$i18n.t('Day')}</option>
					<option value="month">{$i18n.t('Month')}</option>
					<option value="year">{$i18n.t('Year')}</option>
				</select>
			</div>

			<div class="flex items-center gap-0.5">
				<select
					class="dark:bg-gray-900 w-fit pr-6 rounded-sm text-xs bg-transparent outline-hidden"
					value={`${limit}`}
					on:change={(e) => {
						limit = parseInt(e.target.value);
						fetchFiles(false);
					}}
				>
					<option value="30">30</option>
					<option value="60">60</option>
					<option value="120">120</option>
				</select>
			</div>

			{#if isAdmin}
				<div class="flex flex-wrap items-center gap-1">
					<select
						class="dark:bg-gray-900 w-fit pr-6 rounded-sm text-xs bg-transparent outline-hidden {selectedUploaderId
							? ''
							: 'text-content-subtle'}"
						bind:value={selectedUploaderId}
						on:change={() => {
							if (selectedUploaderId !== '') {
								selectedUploaderIds = [...selectedUploaderIds, selectedUploaderId];
								selectedUploaderId = '';
								fetchFiles(false);
							}
						}}
					>
						<option class=" text-content" value="" disabled selected
							>{$i18n.t('Filter by uploader')}</option
						>
						{#each uploaders.filter((u) => !selectedUploaderIds.includes(u.id)) as uploader}
							<option class=" text-content" value={uploader.id}
								>{uploader.name} ({uploader.file_count})</option
							>
						{/each}
					</select>

					{#each selectedUploaderIds as uploaderId}
						{@const uploader = uploaders.find((u) => u.id === uploaderId)}
						<div
							class="flex items-center gap-1 px-2 py-0.5 rounded-full bg-gray-100 dark:bg-gray-850 text-xs"
						>
							<span>{uploader?.name ?? uploaderId}</span>
							<button
								class="rounded-full hover:bg-black/10 dark:hover:bg-white/10 transition"
								type="button"
								on:click={() => {
									selectedUploaderIds = selectedUploaderIds.filter((id) => id !== uploaderId);
									fetchFiles(false);
								}}
							>
								<XMark className="size-3" />
							</button>
						</div>
					{/each}
				</div>
			{/if}
		</div>
	</div>

	{#if files.length === 0}
		<div class="w-full flex justify-center items-center py-16 text-sm text-content-subtle">
			{$i18n.t('No files found')}
		</div>
	{:else if viewMode === 'grid'}
		<div class="mb-5">
			{#each sections as section, sectionIdx (section.label)}
				<div
					class="px-1 py-1.5 w-full text-xs text-content-muted font-medium {sectionIdx > 0
						? 'mt-1.5'
						: ''}"
				>
					{section.label}
				</div>
				<div class="gap-2 grid lg:grid-cols-2 xl:grid-cols-3">
					{#each section.items as file (file.id)}
						<div
							class=" flex space-x-3 cursor-pointer w-full px-3 py-2 dark:hover:bg-white/5 hover:bg-black/5 rounded-xl transition"
						>
							<button
								class=" flex flex-1 min-w-0 items-center gap-3 cursor-pointer text-left"
								type="button"
								on:click={() => openFile(file)}
							>
								{#if isImage(file)}
									<img
										src={`${WEBUI_API_BASE_URL}/files/${file.id}/content`}
										alt={file?.meta?.name ?? file.filename}
										loading="lazy"
										class="size-12 shrink-0 rounded-lg object-cover bg-black/5 dark:bg-white/5"
									/>
								{:else}
									<div
										class="size-12 shrink-0 rounded-lg bg-black/5 dark:bg-white/5 flex items-center justify-center text-content-muted"
									>
										<Document className="size-5" />
									</div>
								{/if}

								<div class=" flex-1 self-center min-w-0">
									<div class=" font-medium line-clamp-1">{file?.meta?.name ?? file.filename}</div>
									<div class=" text-xs text-content-muted line-clamp-1 mt-0.5">
										{#if file?.meta?.size}
											{formatFileSize(file.meta.size)} •
										{/if}
										{formatTimestamp(file?.[orderBy])}
									</div>
								</div>
							</button>

							<div class="flex flex-row gap-0.5 self-center">
								<Tooltip content={visibilityLabel(file)}>
									<button
										class="self-center w-fit text-sm px-2 py-2 text-content-muted hover:text-content hover:bg-black/5 dark:hover:bg-white/5 rounded-xl"
										type="button"
										on:click={() => openAccessControl(file)}
									>
										<FileVisibilityIcon accessControl={file?.access_control ?? null} />
									</button>
								</Tooltip>

								<Tooltip content={$i18n.t('Delete')}>
									<button
										class="self-center w-fit text-sm px-2 py-2 text-content-muted hover:text-content hover:bg-black/5 dark:hover:bg-white/5 rounded-xl"
										type="button"
										on:click={() => {
											deleteTarget = file;
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
		</div>
	{:else}
		<div
			class="scrollbar-hidden relative whitespace-nowrap overflow-x-auto max-w-full rounded-sm pt-0.5 mb-5"
		>
			<table class="w-full text-sm text-left text-content-muted table-auto max-w-full rounded-sm">
				<thead
					class="text-xs text-content-muted uppercase bg-gray-50 dark:bg-gray-850 -translate-y-0.5"
				>
					<tr class="">
						<th scope="col" class="px-3 py-1.5 select-none">{$i18n.t('Name')}</th>
						<th scope="col" class="px-3 py-1.5 select-none">{$i18n.t('Size')}</th>
						<th scope="col" class="px-3 py-1.5 select-none">{$i18n.t('Type')}</th>
						<th
							scope="col"
							class="px-3 py-1.5 cursor-pointer select-none"
							on:click={() => setSortKey('created_at')}
						>
							<div class="flex gap-1.5 items-center">
								{$i18n.t('Created at')}
								{#if orderBy === 'created_at'}
									<span class="font-normal">
										{#if direction === 'asc'}
											<ChevronUp className="size-2" />
										{:else}
											<ChevronDown className="size-2" />
										{/if}
									</span>
								{:else}
									<span class="invisible">
										<ChevronUp className="size-2" />
									</span>
								{/if}
							</div>
						</th>
						<th
							scope="col"
							class="px-3 py-1.5 cursor-pointer select-none"
							on:click={() => setSortKey('accessed_at')}
						>
							<div class="flex gap-1.5 items-center">
								{$i18n.t('Last Accessed')}
								{#if orderBy === 'accessed_at'}
									<span class="font-normal">
										{#if direction === 'asc'}
											<ChevronUp className="size-2" />
										{:else}
											<ChevronDown className="size-2" />
										{/if}
									</span>
								{:else}
									<span class="invisible">
										<ChevronUp className="size-2" />
									</span>
								{/if}
							</div>
						</th>
						<th scope="col" class="px-3 py-1.5 select-none">{$i18n.t('Visibility')}</th>
						<th scope="col" class="px-3 py-2 text-right" />
					</tr>
				</thead>
				<tbody class="">
					{#each sections as section (section.label)}
						<tr>
							<td colspan="7" class="px-3 pt-2 pb-1 text-xs font-medium text-content-muted">
								{section.label}
							</td>
						</tr>
						{#each section.items as file (file.id)}
							<tr
								class="bg-white dark:bg-gray-900 dark:border-gray-850 text-xs cursor-pointer hover:bg-black/5 dark:hover:bg-white/5 transition"
								on:click={() => openFile(file)}
							>
								<td class="px-3 py-1 font-medium text-content">
									<div class="flex items-center gap-2 max-w-72">
										{#if isImage(file)}
											<img
												src={`${WEBUI_API_BASE_URL}/files/${file.id}/content`}
												alt={file?.meta?.name ?? file.filename}
												loading="lazy"
												class="size-6 shrink-0 rounded object-cover bg-black/5 dark:bg-white/5"
											/>
										{/if}
										<div class="font-medium truncate">{file?.meta?.name ?? file.filename}</div>
									</div>
								</td>
								<td class="px-3 py-1">
									{file?.meta?.size ? formatFileSize(file.meta.size) : '-'}
								</td>
								<td class="px-3 py-1">{file?.meta?.content_type ?? '-'}</td>
								<td class="px-3 py-1">{formatTimestamp(file?.created_at)}</td>
								<td class="px-3 py-1">{formatTimestamp(file?.accessed_at)}</td>
								<td class="px-3 py-1">
									<Tooltip content={visibilityLabel(file)}>
										<button
											class="self-center w-fit text-sm px-2 py-2 text-content-muted hover:text-content hover:bg-black/5 dark:hover:bg-white/5 rounded-xl"
											type="button"
											on:click|stopPropagation={() => openAccessControl(file)}
										>
											<FileVisibilityIcon accessControl={file?.access_control ?? null} />
										</button>
									</Tooltip>
								</td>
								<td class="px-3 py-1 text-right">
									<div class="flex justify-end w-full">
										<Tooltip content={$i18n.t('Delete')}>
											<button
												class="self-center w-fit text-sm px-2 py-2 hover:bg-black/5 dark:hover:bg-white/5 rounded-xl"
												type="button"
												on:click|stopPropagation={() => {
													deleteTarget = file;
													showDeleteConfirm = true;
												}}
											>
												<GarbageBin />
											</button>
										</Tooltip>
									</div>
								</td>
							</tr>
						{/each}
					{/each}
				</tbody>
			</table>
		</div>
	{/if}

	{#if files.length < total}
		<div class="w-full flex justify-center pb-6 -mt-2">
			<button
				class="px-3.5 py-1.5 text-sm font-medium rounded-xl hover:bg-black/5 dark:hover:bg-white/5 text-content-muted hover:text-content transition flex items-center gap-2"
				type="button"
				disabled={loading}
				on:click={() => fetchFiles(true)}
			>
				{#if loading}
					<Spinner className="size-3.5" />
				{/if}
				{$i18n.t('Load More')}
			</button>
		</div>
	{/if}

	<AccessControlModal
		bind:show={showAccessControlModal}
		bind:accessControl={acValue}
		variant="file"
	/>
{:else}
	<div class="w-full h-full flex justify-center items-center">
		<Spinner />
	</div>
{/if}
