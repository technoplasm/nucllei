<script lang="ts">
	// llens(#69, #70 A2): ファイル管理ページ。一覧はサーバーページング(GET /files/list)。
	// 非 admin は常に自分の file のみ。admin は uploaders フィルタで他ユーザーを覗ける
	// (選択が空 = 自分の file のみ)。ACL 編集は Skills と同じ「アイコン → モーダル → 閉時 commit」。
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
		getFileChats,
		uploadFile,
		deleteFileById,
		restoreFileById,
		purgeFileById,
		updateFileAccessControlById
	} from '$lib/apis/files';

	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import FileItemModal from '$lib/components/common/FileItemModal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import AccessControlModal from '$lib/components/workspace/common/AccessControlModal.svelte';
	import ChatsListModal from '$lib/components/workspace/common/ChatsListModal.svelte';

	const i18n = getContext('i18n');

	const LIMIT = 60;

	let loaded = $state(false);
	let loading = $state(false);

	let files = $state<any[]>([]);
	let total = $state(0);

	let query = $state('');
	let queryDebounceTimeout: ReturnType<typeof setTimeout> | null = null;

	let viewMode = $state<'grid' | 'table'>('grid');
	let orderBy = $state<'created_at' | 'accessed_at' | 'filename'>('created_at');
	let direction = $state<'desc' | 'asc'>('desc');
	let groupBy = $state<'day' | 'month' | 'year'>('day');

	// admin: uploaders フィルタ(chips)
	let uploaders = $state<any[]>([]);
	let selectedUploaderIds = $state<string[]>([]);
	let selectedUploaderId = $state('');

	// llens(#94): soft delete 済み行の扱い(admin 専用フィルタ。非 admin は常に exclude)
	let deletedFilter = $state<'exclude' | 'only' | 'include'>('exclude');

	const isAdmin = $derived($user?.role === 'admin');

	const fetchFiles = async (append = false) => {
		loading = true;

		const res = await getFileList({
			limit: LIMIT,
			offset: append ? files.length : 0,
			order_by: orderBy,
			direction,
			filename: query !== '' ? query : undefined,
			user_ids: isAdmin
				? selectedUploaderIds.length > 0
					? selectedUploaderIds
					: $user?.id
						? [$user.id]
						: []
				: undefined,
			deleted: isAdmin ? deletedFilter : undefined
		}).catch((err) => {
			toast.error(`${err}`);
			return null;
		});

		if (res) {
			// append 中にサーバー側の並びが変わりうる(accessed_at ソート中のプレビュー等)。
			// 同一 id の重複は keyed each を壊すので除外する。
			files = append
				? [...files, ...res.items.filter((item: any) => !files.some((f) => f.id === item.id))]
				: res.items;
			total = res.total;
			if (!append) {
				// 表示条件(検索・フィルタ・ソート)が変わったら、見えなくなった選択を残さない
				selectedIds = selectedIds.filter((id) => files.some((f) => f.id === id));
			}
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

	// ソート(テーブルヘッダ / セレクタ共通)。キー変更時は自然な向きから(時刻 = 新しい順 / 名前 = A→Z)
	const defaultDirection = (key: typeof orderBy) => (key === 'filename' ? 'asc' : 'desc');

	const setSortKey = (key: typeof orderBy) => {
		if (orderBy === key) {
			direction = direction === 'desc' ? 'asc' : 'desc';
		} else {
			orderBy = key;
			direction = defaultDirection(key);
		}
		fetchFiles(false);
	};

	// 逆順ボタンの状態ラベル(名前順は新旧でなく昇降で言う)
	const directionLabel = $derived(
		orderBy === 'filename'
			? direction === 'desc'
				? $i18n.t('Descending')
				: $i18n.t('Ascending')
			: direction === 'desc'
				? $i18n.t('Newest first')
				: $i18n.t('Oldest first')
	);

	// 期間グルーピング(アクティブなソート時刻を基準に境界検出)
	const computeSections = (files: any[], groupBy: string, orderBy: string) => {
		const labelOf = (file: any) => {
			const d = dayjs((file?.[orderBy] ?? 0) * 1000);
			if (groupBy === 'year') {
				return d.format('YYYY');
			} else if (groupBy === 'month') {
				return d.format('YYYY-MM');
			}
			return d.format('YYYY-MM-DD');
		};

		const sections: { label: string; items: any[] }[] = [];
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

	// 名前順のときは期間グルーピングが意味を持たないので、見出しなしの単一セクションにする
	const sections = $derived(
		orderBy === 'filename'
			? [{ label: '', items: files }]
			: computeSections(files, groupBy, orderBy)
	);

	const isImage = (file: any) => (file?.meta?.content_type ?? '').startsWith('image/');

	// llens(#94): soft delete 状態。purged は filename/meta.name も落ちているので表示名は id へフォールバック
	const isDeleted = (file: any) => !!file?.deleted_at;
	const isPurged = (file: any) => !!file?.purged_at;
	const displayName = (file: any) => file?.meta?.name ?? file?.filename ?? file?.id;

	// deleted_by の名前解決(uploaders は admin が取得済み。引けなければ id 素通し)
	const userName = (id: string | null | undefined) =>
		uploaders.find((u) => u.id === id)?.name ?? id ?? '-';

	// file の共有状態 3 値。file の ACL 意味論(他エンティティと逆):
	// null=非公開 / {read:{public:true}}=全体公開 / {read:{group_ids,user_ids}}=グループ共有
	const visibilityOf = (file: any) => {
		const ac = file?.access_control ?? null;
		if (ac === null) {
			return { icon: 'i-lucide-lock', label: $i18n.t('Private') };
		} else if (ac?.read?.public) {
			return { icon: 'i-lucide-globe', label: $i18n.t('Public') };
		}
		return { icon: 'i-lucide-users', label: $i18n.t('Shared') };
	};

	const formatTimestamp = (ts: number | null | undefined) =>
		dayjs((ts ?? 0) * 1000).format('YYYY-MM-DD HH:mm');

	// アップロード(複数可)
	let fileInput = $state<HTMLInputElement | null>(null);
	let uploading = $state(false);

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

	// ---- 複数選択: サムネイル(アイコン)hover でチェックボックス化 → 一括削除/共有 ----
	let selectedIds = $state<string[]>([]);
	const selectionActive = $derived(selectedIds.length > 0);
	const selectedFiles = $derived(files.filter((f) => selectedIds.includes(f.id)));
	const allSelected = $derived(files.length > 0 && files.every((f) => selectedIds.includes(f.id)));
	// #94: deleted 行は共有(ACL 更新)不可 — 選択が全部 deleted なら一括 Share を無効化
	const bulkShareEnabled = $derived(selectedFiles.some((f) => !isDeleted(f)));

	const toggleSelect = (file: any) => {
		selectedIds = selectedIds.includes(file.id)
			? selectedIds.filter((id) => id !== file.id)
			: [...selectedIds, file.id];
	};

	const toggleSelectAll = () => {
		selectedIds = allSelected ? [] : files.map((f) => f.id);
	};

	// visibility(access_control)の一覧内編集: アイコン → AccessControlModal、閉じたら保存。
	// 一括編集(acBulk)は選択中の全 file に同じ ACL を適用する
	let showAccessControlModal = $state(false);
	let acItem = $state<any>(null);
	let acValue = $state<any>(null);
	let acBulk = $state(false);

	const openAccessControl = (file: any) => {
		acItem = file;
		acValue = file?.access_control ?? null;
		acBulk = false;
		showAccessControlModal = true;
	};

	const openBulkAccessControl = () => {
		// 全選択 file の ACL が一致していればそれを初期値に、混在なら非公開から始める
		// (#94: deleted 行は commit 対象外なので初期値の算定からも外す)
		const acs = selectedFiles
			.filter((f) => !isDeleted(f))
			.map((f) => JSON.stringify(f?.access_control ?? null));
		acValue = acs.length > 0 && acs.every((s) => s === acs[0]) ? JSON.parse(acs[0]) : null;
		acItem = null;
		acBulk = true;
		showAccessControlModal = true;
	};

	// 一覧の行内ボタン: 選択中の file に対する操作は選択全体への一括操作になる
	const requestAccessControl = (file: any) => {
		if (selectedIds.includes(file.id)) {
			openBulkAccessControl();
		} else {
			openAccessControl(file);
		}
	};

	const requestDelete = (file: any) => {
		if (selectedIds.includes(file.id)) {
			deleteBulk = true;
		} else {
			deleteBulk = false;
			deleteTarget = file;
		}
		showDeleteConfirm = true;
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

	const commitBulkAccessControl = async () => {
		acBulk = false;
		const value = acValue;

		// #94: deleted 行への access_control/update は admin でも 404 の契約 — 対象から外す
		for (const f of selectedFiles.filter((f) => !isDeleted(f))) {
			const res = await updateFileAccessControlById(f.id, value).catch((err) => {
				toast.error(`${displayName(f)}: ${err}`);
				return null;
			});
			if (res) {
				files = files.map((x) =>
					x.id === f.id ? { ...x, access_control: res.access_control ?? null } : x
				);
			}
		}
	};

	$effect(() => {
		if (!showAccessControlModal) {
			if (acBulk) {
				commitBulkAccessControl();
			} else if (acItem) {
				commitAccessControl();
			}
		}
	});

	// 閲覧モーダル(/files/list は content を含まないため、開く前に getFileById で data 込みを取得)
	let showFileModal = $state(false);
	let selectedItem = $state<any>(null);

	const openFile = async (file: any) => {
		// purged は実体も抽出テキストも無い(#94)— 閲覧導線ごと落とす
		if (isPurged(file)) {
			return;
		}
		// getFileById は status 付き Error を投げる(#94)— 表示は message で
		const res = await getFileById(file.id).catch((err) => {
			toast.error(`${err?.message ?? err}`);
			return null;
		});
		if (!res) return;

		selectedItem = {
			id: res.id,
			name: res?.meta?.name ?? res?.filename ?? displayName(file),
			size: res?.meta?.size,
			file: res
		};
		showFileModal = true;
	};

	// 関連チャット(この file を参照しているチャットの一覧モーダル。開くたびに取得)
	let showChatsModal = $state(false);
	let chatsFile = $state<any>(null);
	let fileChats = $state<any[] | null>(null);

	const openFileChats = (file: any) => {
		chatsFile = file;
		fileChats = null;
		showChatsModal = true;
		getFileChats(file.id)
			.then((res) => {
				fileChats = res ?? [];
			})
			.catch((err) => {
				toast.error(`${err}`);
				fileChats = [];
			});
	};

	// 削除(deleteBulk = 選択中の一括削除)
	let showDeleteConfirm = $state(false);
	let deleteTarget = $state<any>(null);
	let deleteBulk = $state(false);

	const deleteHandler = async (file: any) => {
		const res = await deleteFileById(file.id).catch((err) => {
			toast.error(`${err}`);
			return null;
		});

		if (res) {
			if (deletedFilter === 'exclude') {
				files = files.filter((f) => f.id !== file.id);
				total = Math.max(0, total - 1);
				selectedIds = selectedIds.filter((id) => id !== file.id);
			} else {
				// 「削除済みのみ / すべて」表示では行が残る(#94 soft delete)— 再取得で deleted_at を反映
				await fetchFiles(false);
			}
		}
	};

	const bulkDeleteHandler = async () => {
		let deleted = false;
		for (const f of selectedFiles) {
			const res = await deleteFileById(f.id).catch((err) => {
				toast.error(`${displayName(f)}: ${err}`);
				return null;
			});
			if (res) {
				deleted = true;
				if (deletedFilter === 'exclude') {
					files = files.filter((x) => x.id !== f.id);
					total = Math.max(0, total - 1);
					selectedIds = selectedIds.filter((id) => id !== f.id);
				}
			}
		}
		if (deleted && deletedFilter !== 'exclude') {
			await fetchFiles(false);
		}
	};

	// llens(#94): admin の復元 / 実体破棄。表示条件次第で行の見え方が変わるので再取得で反映する
	let showPurgeConfirm = $state(false);
	let purgeTarget = $state<any>(null);

	const restoreHandler = async (file: any) => {
		const res = await restoreFileById(file.id).catch((err) => {
			toast.error(`${err}`);
			return null;
		});
		if (res) {
			await fetchFiles(false);
		}
	};

	const purgeHandler = async (file: any) => {
		const res = await purgeFileById(file.id).catch((err) => {
			toast.error(`${err}`);
			return null;
		});
		if (res) {
			await fetchFiles(false);
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
	<ConfirmDialog
		bind:show={showDeleteConfirm}
		title={deleteBulk
			? $i18n.t('Delete {{n}} files?', { n: selectedFiles.length })
			: $i18n.t('Delete file?')}
		onConfirm={() => {
			if (deleteBulk) {
				bulkDeleteHandler();
			} else {
				deleteHandler(deleteTarget);
			}
		}}
	>
		<div class=" text-sm text-content-muted">
			{$i18n.t('This will delete')}
			{#if deleteBulk}
				<div class="mt-1 max-h-40 overflow-y-auto">
					{#each selectedFiles as f (f.id)}
						<div class="font-semibold truncate">{displayName(f)}</div>
					{/each}
				</div>
			{:else}
				<span class=" font-semibold">{deleteTarget?.meta?.name ?? deleteTarget?.filename}</span>.
			{/if}
			<!-- #94: soft delete — 添付として使われたチャット上では「削除済み」として表示が残る -->
			<div class="mt-1.5">
				{$i18n.t('Chats that used this file will keep showing it as deleted.')}
			</div>
		</div>
	</ConfirmDialog>

	<!-- #94: purge(実体破棄)は「削除」と別の不可逆操作 — 確認で復元不能を明示する -->
	<ConfirmDialog
		bind:show={showPurgeConfirm}
		title={$i18n.t('Purge file content?')}
		onConfirm={() => {
			purgeHandler(purgeTarget);
		}}
	>
		<div class=" text-sm text-content-muted">
			<span class=" font-semibold">{displayName(purgeTarget)}</span>
			<div class="mt-1.5">
				{$i18n.t('The file content will be destroyed and cannot be restored.')}
			</div>
		</div>
	</ConfirmDialog>

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
				deleteBulk = false;
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
		onchange={async (e) => {
			await uploadHandler(Array.from(e.currentTarget?.files ?? []));
			if (fileInput) {
				fileInput.value = '';
			}
		}}
	/>

	<div class="flex flex-col gap-1 mt-1.5">
		<div class="flex items-center text-xl font-medium px-0.5">
			{$i18n.t('Files')}
		</div>

		<!-- 狭幅は models 同型の 2 行折り返し(Upload ←端寄せ→ 表示コントロール / 検索は全幅)。
		 DOM は 1 セットのまま flex の order + wrap で並べ替える({#if} 二重化はしない)。 -->
		<div class="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2">
			<button
				class="order-1 shrink-0 whitespace-nowrap inline-flex items-center py-0.5 pl-2 pr-3 rounded-full bg-primary hover:bg-primary-hover text-primary-content font-medium text-sm"
				type="button"
				disabled={uploading}
				onclick={() => {
					fileInput?.click();
				}}
			>
				{#if uploading}
					<Spinner className="size-3.5 mr-1" />
				{:else}
					<span class="i-lucide-upload size-3.5 block mr-1"></span>
				{/if}
				{$i18n.t('Upload Files')}
			</button>

			<div
				class="order-3 w-full sm:order-2 sm:w-auto flex items-center px-2 bg-surface-form rounded-xs"
			>
				<span class="i-lucide-search size-3.5 block shrink-0"></span>
				<input
					class="w-full sm:w-48 text-sm px-2 py-1 bg-transparent outline-hidden"
					bind:value={query}
					oninput={onQueryInput}
					placeholder={$i18n.t('Search Files')}
				/>
			</div>

			<!-- 表示形式 + 表示逆順(逆順は view 切替の隣に置く) -->
			<div class="order-2 ml-auto sm:order-3 sm:ml-0 flex items-center gap-0.5">
				<Tooltip content={$i18n.t('Card View')}>
					<button
						class="p-1.5 rounded-lg {viewMode === 'grid'
							? 'bg-surface-raised text-content'
							: 'text-content-muted hover:bg-surface-hover'}"
						type="button"
						aria-label={$i18n.t('Card View')}
						onclick={() => setViewMode('grid')}
					>
						<span class="i-lucide-layout-grid size-4 block"></span>
					</button>
				</Tooltip>
				<Tooltip content={$i18n.t('Table View')}>
					<button
						class="p-1.5 rounded-lg {viewMode === 'table'
							? 'bg-surface-raised text-content'
							: 'text-content-muted hover:bg-surface-hover'}"
						type="button"
						aria-label={$i18n.t('Table View')}
						onclick={() => setViewMode('table')}
					>
						<span class="i-lucide-list size-4 block"></span>
					</button>
				</Tooltip>
				<Tooltip content={directionLabel}>
					<button
						class="p-1.5 rounded-lg text-content-muted hover:bg-surface-hover"
						type="button"
						aria-label={directionLabel}
						onclick={() => {
							direction = direction === 'desc' ? 'asc' : 'desc';
							fetchFiles(false);
						}}
					>
						<span
							class="{direction === 'desc'
								? 'i-lucide-arrow-down-wide-narrow'
								: 'i-lucide-arrow-up-narrow-wide'} size-4 block"
						></span>
					</button>
				</Tooltip>
			</div>
		</div>

		<!-- 並び替え・期間グルーピング・(admin)アップロード者フィルタ -->
		<div class="mt-1 flex flex-wrap items-center gap-x-4 gap-y-2 px-0.5 text-xs text-content-muted">
			<label class="flex items-center gap-1.5">
				{$i18n.t('Sort by')}
				<select
					class="bg-surface-form hover:bg-surface-hover w-fit px-2 py-0.5 pr-6 rounded-xs text-sm outline-hidden"
					bind:value={orderBy}
					onchange={() => {
						direction = defaultDirection(orderBy);
						fetchFiles(false);
					}}
				>
					<option value="created_at">{$i18n.t('Created at')}</option>
					<option value="accessed_at">{$i18n.t('Last Accessed')}</option>
					<option value="filename">{$i18n.t('Name')}</option>
				</select>
			</label>

			{#if orderBy !== 'filename'}
				<label class="flex items-center gap-1.5">
					{$i18n.t('Group by')}
					<select
						class="bg-surface-form hover:bg-surface-hover w-fit px-2 py-0.5 pr-6 rounded-xs text-sm outline-hidden"
						bind:value={groupBy}
					>
						<option value="day">{$i18n.t('Day')}</option>
						<option value="month">{$i18n.t('Month')}</option>
						<option value="year">{$i18n.t('Year')}</option>
					</select>
				</label>
			{/if}

			{#if isAdmin}
				<!-- #94: soft delete 済み行のフィルタ(admin のみ。既定 = 普通のファイルのみ) -->
				<select
					class="bg-surface-form hover:bg-surface-hover w-fit px-2 py-0.5 pr-6 rounded-xs text-sm outline-hidden {deletedFilter !==
					'exclude'
						? ''
						: 'text-content-subtle'}"
					bind:value={deletedFilter}
					onchange={() => {
						fetchFiles(false);
					}}
				>
					<option class=" text-content" value="exclude">{$i18n.t('Active files only')}</option>
					<option class=" text-content" value="only">{$i18n.t('Deleted only')}</option>
					<option class=" text-content" value="include">{$i18n.t('All')}</option>
				</select>

				<div class="flex flex-wrap items-center gap-1">
					<select
						class="bg-surface-form hover:bg-surface-hover w-fit px-2 py-0.5 pr-6 rounded-xs text-sm outline-hidden {selectedUploaderId
							? ''
							: 'text-content-subtle'}"
						bind:value={selectedUploaderId}
						onchange={() => {
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
						<div class="flex items-center gap-1 px-2 py-0.5 rounded-full bg-surface-raised text-xs">
							<span>{uploader?.name ?? uploaderId}</span>
							<button
								class="rounded-full hover:bg-surface-hover"
								type="button"
								aria-label={$i18n.t('Delete')}
								onclick={() => {
									selectedUploaderIds = selectedUploaderIds.filter((id) => id !== uploaderId);
									fetchFiles(false);
								}}
							>
								<span class="i-lucide-x size-3 block"></span>
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
				{#if section.label}
					<div class="text-md text-content-muted {sectionIdx > 0 ? 'mt-3' : 'mt-6'} mb-1">
						{section.label}
					</div>
				{/if}
				<div class="gap-3 grid lg:grid-cols-2 xl:grid-cols-3 {section.label ? '' : 'mt-6'}">
					{#each section.items as file (file.id)}
						{@const vis = visibilityOf(file)}
						{@const selected = selectedIds.includes(file.id)}
						{@const deleted = isDeleted(file)}
						{@const purged = isPurged(file)}
						<!-- カード全体がクリック=閲覧(button の after オーバーレイ)。右のボタン類は relative で上に載せる。
						 models と違い全面クリックも button なので、hover 抑制は .card-action に限定する -->
						<div
							class="relative flex items-center gap-0.5 px-4 py-3 bg-surface-raised [&:hover:not(:has(.card-action:hover))]:bg-surface-hover rounded-xl {selected
								? 'ring-2 ring-primary'
								: ''}"
						>
							<!-- サムネイルは選択トグル: hover(または選択モード中)でチェックボックスに変わる。
							 全面クリックの after オーバーレイより手前に置くため z-10 -->
							<button
								class="card-action group/thumb relative z-10 shrink-0 mr-2.5 cursor-pointer {deleted
									? 'opacity-60'
									: ''}"
								type="button"
								aria-label={$i18n.t('Select')}
								aria-pressed={selected}
								onclick={() => toggleSelect(file)}
							>
								{#if isImage(file)}
									<img
										src={`${WEBUI_API_BASE_URL}/files/${file.id}/content`}
										alt={displayName(file)}
										loading="lazy"
										class="size-12 rounded-lg object-cover bg-black/5 dark:bg-white/5"
									/>
								{:else}
									<div
										class="size-12 rounded-lg bg-black/5 dark:bg-white/5 flex items-center justify-center text-content-muted"
									>
										<span class="i-lucide-file-text size-5 block"></span>
									</div>
								{/if}
								<div
									class="absolute inset-0 rounded-lg flex items-center justify-center bg-surface-raised/85 {selected ||
									selectionActive
										? ''
										: 'opacity-0 group-hover/thumb:opacity-100'}"
								>
									<span
										class="{selected
											? 'i-lucide-square-check-big text-content'
											: 'i-lucide-square text-content-muted'} size-5 block"
									></span>
								</div>
							</button>

							<!-- #94: purged は実体が無く開けないのでクリック導線を落とす(openFile 側でもガード) -->
							<button
								class="flex items-center gap-3 flex-1 min-w-0 text-left after:absolute after:inset-0 after:content-[''] {purged
									? 'cursor-default'
									: 'cursor-pointer'}"
								type="button"
								onclick={() => openFile(file)}
							>
								<div class="flex-1 self-center min-w-0">
									<div class="flex items-center gap-1.5 min-w-0">
										<div class="font-medium truncate {deleted ? 'opacity-60' : ''}">
											{displayName(file)}
										</div>
										{#if deleted}
											<span
												class="px-1.5 py-0.5 rounded-full bg-red-500/20 text-[10px] font-normal text-red-700 dark:text-red-200 shrink-0"
												>{$i18n.t('Deleted')}</span
											>
										{/if}
										{#if purged}
											<span
												class="px-1.5 py-0.5 rounded-full bg-surface-muted text-[10px] font-normal text-content-muted shrink-0"
												>{$i18n.t('Purged')}</span
											>
										{/if}
									</div>
									<div class="text-xs text-content-muted line-clamp-1 mt-0.5">
										{#if file?.meta?.size}
											{formatFileSize(file.meta.size)} •
										{/if}
										{formatTimestamp(orderBy === 'filename' ? file?.created_at : file?.[orderBy])}
									</div>
									{#if deleted}
										<div class="text-xs text-content-muted line-clamp-1 mt-0.5">
											{$i18n.t('Deleted')}: {formatTimestamp(file.deleted_at)} • {userName(
												file.deleted_by
											)}
										</div>
									{/if}
								</div>
							</button>

							<Tooltip className="relative flex" content={$i18n.t('Related chats')}>
								<button
									class="card-action px-2 py-2 text-content bg-transparent hover:bg-surface-hover rounded-xl"
									type="button"
									aria-label={$i18n.t('Related chats')}
									onclick={() => openFileChats(file)}
								>
									<span class="i-lucide-message-square size-4 block"></span>
								</button>
							</Tooltip>

							{#if !deleted}
								<Tooltip className="relative flex" content={vis.label}>
									<button
										class="card-action px-2 py-2 text-content bg-transparent hover:bg-surface-hover rounded-xl"
										type="button"
										aria-label={vis.label}
										onclick={() => requestAccessControl(file)}
									>
										<span class="{vis.icon} size-4 block"></span>
									</button>
								</Tooltip>

								<Tooltip className="relative flex" content={$i18n.t('Delete')}>
									<button
										class="card-action px-2 py-2 text-content bg-transparent hover:bg-surface-hover rounded-xl"
										type="button"
										aria-label={$i18n.t('Delete')}
										onclick={() => requestDelete(file)}
									>
										<span class="i-lucide-trash-2 size-4 block"></span>
									</button>
								</Tooltip>
							{:else if !purged}
								<!-- #94: 削除済み行の admin 操作 — 復元 / 実体破棄。purged は操作なし -->
								<Tooltip className="relative flex" content={$i18n.t('Restore')}>
									<button
										class="card-action px-2 py-2 text-content bg-transparent hover:bg-surface-hover rounded-xl"
										type="button"
										aria-label={$i18n.t('Restore')}
										onclick={() => restoreHandler(file)}
									>
										<span class="i-lucide-rotate-ccw size-4 block"></span>
									</button>
								</Tooltip>

								<Tooltip className="relative flex" content={$i18n.t('Purge file content')}>
									<button
										class="card-action px-2 py-2 text-red-600 dark:text-red-400 bg-transparent hover:bg-surface-hover rounded-xl"
										type="button"
										aria-label={$i18n.t('Purge file content')}
										onclick={() => {
											purgeTarget = file;
											showPurgeConfirm = true;
										}}
									>
										<span class="i-lucide-flame size-4 block"></span>
									</button>
								</Tooltip>
							{/if}
						</div>
					{/each}
				</div>
			{/each}
		</div>
	{:else}
		<div class="relative whitespace-nowrap overflow-x-auto max-w-full rounded-sm mt-6 mb-5">
			<table class="w-full text-sm text-left text-content-muted table-auto max-w-full rounded-sm">
				<thead class="text-xs text-content-muted uppercase bg-surface-muted">
					<tr>
						<th scope="col" class="w-8 pl-3 pr-0 py-1.5">
							<button
								class="flex cursor-pointer"
								type="button"
								aria-label={$i18n.t('Select all')}
								aria-pressed={allSelected}
								onclick={toggleSelectAll}
							>
								<span
									class="{allSelected
										? 'i-lucide-square-check-big text-content'
										: 'i-lucide-square'} size-4 block"
								></span>
							</button>
						</th>
						<th
							scope="col"
							class="px-3 py-1.5 cursor-pointer select-none"
							onclick={() => setSortKey('filename')}
						>
							<div class="flex gap-1.5 items-center">
								{$i18n.t('Name')}
								<span
									class="size-2.5 block {orderBy === 'filename'
										? direction === 'asc'
											? 'i-lucide-chevron-up'
											: 'i-lucide-chevron-down'
										: 'i-lucide-chevron-up invisible'}"
								></span>
							</div>
						</th>
						<th scope="col" class="px-3 py-1.5 select-none">{$i18n.t('Size')}</th>
						<th scope="col" class="px-3 py-1.5 select-none">{$i18n.t('Type')}</th>
						<th
							scope="col"
							class="px-3 py-1.5 cursor-pointer select-none"
							onclick={() => setSortKey('created_at')}
						>
							<div class="flex gap-1.5 items-center">
								{$i18n.t('Created at')}
								<span
									class="size-2.5 block {orderBy === 'created_at'
										? direction === 'asc'
											? 'i-lucide-chevron-up'
											: 'i-lucide-chevron-down'
										: 'i-lucide-chevron-up invisible'}"
								></span>
							</div>
						</th>
						<th
							scope="col"
							class="px-3 py-1.5 cursor-pointer select-none"
							onclick={() => setSortKey('accessed_at')}
						>
							<div class="flex gap-1.5 items-center">
								{$i18n.t('Last Accessed')}
								<span
									class="size-2.5 block {orderBy === 'accessed_at'
										? direction === 'asc'
											? 'i-lucide-chevron-up'
											: 'i-lucide-chevron-down'
										: 'i-lucide-chevron-up invisible'}"
								></span>
							</div>
						</th>
						{#if deletedFilter !== 'exclude'}
							<!-- #94: 削除済みを表示中だけ deleted_at / deleted_by 列を出す -->
							<th scope="col" class="px-3 py-1.5 select-none">{$i18n.t('Deleted at')}</th>
							<th scope="col" class="px-3 py-1.5 select-none">{$i18n.t('Deleted by')}</th>
						{/if}
						<th scope="col" class="px-3 py-2 text-right"></th>
					</tr>
				</thead>
				<tbody>
					{#each sections as section (section.label)}
						{#if section.label}
							<tr>
								<td
									colspan={deletedFilter !== 'exclude' ? 9 : 7}
									class="px-3 pt-2 pb-1 text-xs font-medium text-content-muted"
								>
									{section.label}
								</td>
							</tr>
						{/if}
						{#each section.items as file (file.id)}
							{@const vis = visibilityOf(file)}
							{@const selected = selectedIds.includes(file.id)}
							{@const deleted = isDeleted(file)}
							{@const purged = isPurged(file)}
							<tr
								class="group text-xs [&:hover:not(:has(button:hover))]:bg-surface-hover {purged
									? 'cursor-default'
									: 'cursor-pointer'} {selected ? 'bg-surface-hover' : 'bg-surface-raised'}"
								onclick={() => openFile(file)}
							>
								<td class="w-8 pl-3 pr-0 py-1">
									<!-- 行 hover(または選択モード中)でチェックボックスを出す -->
									<button
										class="flex py-1 cursor-pointer {selected || selectionActive
											? ''
											: 'opacity-0 group-hover:opacity-100'}"
										type="button"
										aria-label={$i18n.t('Select')}
										aria-pressed={selected}
										onclick={(e) => {
											e.stopPropagation();
											toggleSelect(file);
										}}
									>
										<span
											class="{selected
												? 'i-lucide-square-check-big text-content'
												: 'i-lucide-square text-content-muted'} size-4 block"
										></span>
									</button>
								</td>
								<td class="px-3 py-1 font-medium text-content">
									<div class="flex items-center gap-2 max-w-72">
										{#if isImage(file)}
											<img
												src={`${WEBUI_API_BASE_URL}/files/${file.id}/content`}
												alt={displayName(file)}
												loading="lazy"
												class="size-6 shrink-0 rounded object-cover bg-black/5 dark:bg-white/5 {deleted
													? 'opacity-60'
													: ''}"
											/>
										{/if}
										<div class="font-medium truncate {deleted ? 'opacity-60' : ''}">
											{displayName(file)}
										</div>
										{#if deleted}
											<span
												class="px-1.5 py-0.5 rounded-full bg-red-500/20 text-[10px] font-normal text-red-700 dark:text-red-200 shrink-0"
												>{$i18n.t('Deleted')}</span
											>
										{/if}
										{#if purged}
											<span
												class="px-1.5 py-0.5 rounded-full bg-surface-muted text-[10px] font-normal text-content-muted shrink-0"
												>{$i18n.t('Purged')}</span
											>
										{/if}
									</div>
								</td>
								<td class="px-3 py-1">
									{file?.meta?.size ? formatFileSize(file.meta.size) : '-'}
								</td>
								<td class="px-3 py-1">{file?.meta?.content_type ?? '-'}</td>
								<td class="px-3 py-1">{formatTimestamp(file?.created_at)}</td>
								<td class="px-3 py-1">{formatTimestamp(file?.accessed_at)}</td>
								{#if deletedFilter !== 'exclude'}
									<td class="px-3 py-1">{deleted ? formatTimestamp(file.deleted_at) : '-'}</td>
									<td class="px-3 py-1">
										<div class="max-w-32 truncate">{deleted ? userName(file.deleted_by) : '-'}</div>
									</td>
								{/if}
								<td class="px-3 py-1 text-right">
									<!-- カードと同構成の 1 セル(関連チャット / 共有状態 / 削除) -->
									<div class="flex justify-end w-full">
										<Tooltip content={$i18n.t('Related chats')}>
											<button
												class="px-2 py-2 text-content bg-transparent hover:bg-surface-hover rounded-xl"
												type="button"
												aria-label={$i18n.t('Related chats')}
												onclick={(e) => {
													e.stopPropagation();
													openFileChats(file);
												}}
											>
												<span class="i-lucide-message-square size-4 block"></span>
											</button>
										</Tooltip>

										{#if !deleted}
											<Tooltip content={vis.label}>
												<button
													class="px-2 py-2 text-content bg-transparent hover:bg-surface-hover rounded-xl"
													type="button"
													aria-label={vis.label}
													onclick={(e) => {
														e.stopPropagation();
														requestAccessControl(file);
													}}
												>
													<span class="{vis.icon} size-4 block"></span>
												</button>
											</Tooltip>

											<Tooltip content={$i18n.t('Delete')}>
												<button
													class="px-2 py-2 text-content bg-transparent hover:bg-surface-hover rounded-xl"
													type="button"
													aria-label={$i18n.t('Delete')}
													onclick={(e) => {
														e.stopPropagation();
														requestDelete(file);
													}}
												>
													<span class="i-lucide-trash-2 size-4 block"></span>
												</button>
											</Tooltip>
										{:else if !purged}
											<!-- #94: 削除済み行の admin 操作 — 復元 / 実体破棄。purged は操作なし -->
											<Tooltip content={$i18n.t('Restore')}>
												<button
													class="px-2 py-2 text-content bg-transparent hover:bg-surface-hover rounded-xl"
													type="button"
													aria-label={$i18n.t('Restore')}
													onclick={(e) => {
														e.stopPropagation();
														restoreHandler(file);
													}}
												>
													<span class="i-lucide-rotate-ccw size-4 block"></span>
												</button>
											</Tooltip>

											<Tooltip content={$i18n.t('Purge file content')}>
												<button
													class="px-2 py-2 text-red-600 dark:text-red-400 bg-transparent hover:bg-surface-hover rounded-xl"
													type="button"
													aria-label={$i18n.t('Purge file content')}
													onclick={(e) => {
														e.stopPropagation();
														purgeTarget = file;
														showPurgeConfirm = true;
													}}
												>
													<span class="i-lucide-flame size-4 block"></span>
												</button>
											</Tooltip>
										{/if}
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
				class="px-3.5 py-1.5 text-sm font-medium rounded-lg hover:bg-surface-hover text-content-muted hover:text-content flex items-center gap-2"
				type="button"
				disabled={loading}
				onclick={() => fetchFiles(true)}
			>
				{#if loading}
					<Spinner className="size-3.5" />
				{/if}
				{$i18n.t('Load More')}
			</button>
		</div>
	{/if}

	<!-- 選択中の一括操作バー(スクロールしても画面下に留まる) -->
	{#if selectionActive}
		<div class="sticky bottom-4 z-30 flex justify-center pb-1 pointer-events-none">
			<div
				class="pointer-events-auto flex items-center gap-0.5 pl-4 pr-1.5 py-1 bg-surface-raised border border-line rounded-full shadow-lg text-sm"
			>
				<span class="font-medium whitespace-nowrap">
					{$i18n.t('{{n}} selected', { n: selectedIds.length })}
				</span>
				<div class="w-px self-stretch my-1.5 bg-line mx-2"></div>
				<!-- #94: 選択が全部 deleted のときは共有先が無い — disabled -->
				<Tooltip content={$i18n.t('Share')}>
					<button
						class="p-2 rounded-full {bulkShareEnabled
							? 'text-content hover:bg-surface-hover'
							: 'text-content-subtle cursor-not-allowed'}"
						type="button"
						aria-label={$i18n.t('Share')}
						disabled={!bulkShareEnabled}
						onclick={openBulkAccessControl}
					>
						<span class="i-lucide-users size-4 block"></span>
					</button>
				</Tooltip>
				<Tooltip content={$i18n.t('Delete')}>
					<button
						class="p-2 rounded-full text-content hover:bg-surface-hover"
						type="button"
						aria-label={$i18n.t('Delete')}
						onclick={() => {
							deleteBulk = true;
							showDeleteConfirm = true;
						}}
					>
						<span class="i-lucide-trash-2 size-4 block"></span>
					</button>
				</Tooltip>
				<Tooltip content={$i18n.t('Clear selection')}>
					<button
						class="p-2 rounded-full text-content hover:bg-surface-hover"
						type="button"
						aria-label={$i18n.t('Clear selection')}
						onclick={() => (selectedIds = [])}
					>
						<span class="i-lucide-x size-4 block"></span>
					</button>
				</Tooltip>
			</div>
		</div>
	{/if}

	<AccessControlModal
		bind:show={showAccessControlModal}
		bind:accessControl={acValue}
		variant="file"
		description={acBulk
			? $i18n.t('{{n}} files selected', { n: selectedFiles.length })
			: (acItem?.meta?.name ?? acItem?.filename ?? '')}
	/>

	<ChatsListModal
		bind:show={showChatsModal}
		title={$i18n.t('Related chats')}
		subtitle={chatsFile ? displayName(chatsFile) : ''}
		chats={fileChats}
	/>
{:else}
	<div class="w-full h-full flex justify-center items-center">
		<Spinner />
	</div>
{/if}
