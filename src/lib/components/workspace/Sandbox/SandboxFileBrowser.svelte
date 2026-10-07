<script lang="ts">
	// #150: sandbox のファイルブラウザ。開ける場所は本人の /workspace と、ACL(#152)で見える
	// /ext/public・/ext/groups/<g> だけ(一覧はサーバーの /places — 判定は nucllei/utils/ext_acl.py)。
	// 書けない場所では書き換えのボタン自体を出さない(サーバーも 403 で止める)。
	// 場所・ディレクトリ・開いているファイルは URL のクエリ(?path= &file=)に持つ — 戻るボタンと再読み込みで戻れる。
	// 会話へ添付する経路は作らない(会話に載せるのは exports/ 一本)。
	import { getContext, onMount, untrack } from 'svelte';
	import { toast } from 'svelte-sonner';
	import dayjs from 'dayjs';

	import { page } from '$app/stores';
	import { beforeNavigate, goto } from '$app/navigation';

	import {
		getSandboxPlaces,
		getSandboxUsage,
		listSandboxDir,
		uploadSandboxFile,
		writeSandboxFile,
		deleteSandboxPath,
		renameSandboxPath,
		makeSandboxDir,
		sandboxDownloadUrl,
		SandboxFilesError,
		type SandboxEntry,
		type SandboxListing,
		type SandboxPlace,
		type SandboxUsage
	} from '$lib/apis/sandbox';
	import { formatFileSize } from '$lib/utils';

	import { DropdownMenu } from 'bits-ui';

	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import DropdownContent from '$lib/components/common/DropdownContent.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import UsageGauge from '$lib/components/common/UsageGauge.svelte';
	import SandboxFileView from './SandboxFileView.svelte';

	const i18n = getContext('i18n');

	const WORKSPACE = '/workspace';
	// imports/ exports/ は Files の写し(#149)、trash/ は /workspace の「削除」の移し先。3 つとも
	// 直下のエントリ(ファイルでもフォルダでも 1 つ)が max(mtime, ctime) から cache_ttl_days 日で
	// 中身ごと消える(sandbox の `_sweep_entries`)。mv は mtime を変えず ctime を今にする
	const TRASH = `${WORKSPACE}/trash`;
	const CACHE_DIRS = [`${WORKSPACE}/imports`, `${WORKSPACE}/exports`];
	const EXPIRING_DIRS = [...CACHE_DIRS, TRASH];
	const inside = (path: string, d: string) => path === d || path.startsWith(`${d}/`);

	let places = $state<SandboxPlace[]>([]);
	let placesLoaded = $state(false);
	let placesError = $state('');
	// テキストの上限(保存の 413 の既定の表示)、アップロードの上限(null = サーバーが知らない)、
	// アップロードの権限(無ければボタンもドロップも出さない — 新規作成・保存は別)
	let textLimit = $state(0);
	let uploadLimit = $state<number | null>(null);
	let canUpload = $state(false);

	let dir = $state(WORKSPACE);
	let openFile = $state<string | null>(null);
	let fileDirty = $state(false);
	// 利用者が「破棄」を選んだ直後の遷移は確認しない
	let skipDirtyCheck = false;

	let listing = $state<SandboxListing | null>(null);
	let listError = $state('');
	let loading = $state(false);
	let usage = $state<SandboxUsage | null>(null);
	let busy = $state(false);

	const placeOf = (path: string) =>
		places
			.filter((p) => path === p.path || path.startsWith(`${p.path}/`))
			.sort((a, b) => b.path.length - a.path.length)[0] ?? null;

	const place = $derived(placeOf(dir));
	const rw = $derived(!!place?.rw && !!listing?.rw);

	// workspace の root の lost+found は ext4 の産物なので一覧に出さない
	const entries = $derived(
		(listing?.entries ?? []).filter((e) => !(dir === WORKSPACE && e.name === 'lost+found'))
	);

	const placeIcon = (p: SandboxPlace | null) =>
		p?.kind === 'public'
			? 'i-lucide-globe'
			: p?.kind === 'group'
				? 'i-lucide-users'
				: 'i-lucide-hard-drive';

	// 場所の切り替え(パンくずとは別のコントロール。ラベルは実パス)
	let showPlaces = $state(false);
	// 書けるかは一覧の応答(listing.rw)まで見る。一覧が来る前は場所の rw で判断
	const readOnly = $derived(!!place && (listing ? !rw : !place.rw));

	// パンくず: 実パスに見える形 `/ext/public / recipes / x.md`。先頭は場所の root を割らずに 1 要素、
	// ファイルを開いているときは末尾にファイル名。末尾(今いる所)はクリック不可。
	// 深いときは「root / … / 末尾 2 つ」に畳み、… で全部を出す。
	const parentOf = (path: string) => path.slice(0, path.lastIndexOf('/')) || '/';
	const crumbBase = $derived(openFile ? parentOf(openFile) : dir);
	const crumbs = $derived.by(() => {
		const p = placeOf(crumbBase);
		if (!p) {
			return [];
		}
		const out = [{ label: p.path, path: p.path }];
		let acc = p.path;
		for (const seg of crumbBase.slice(p.path.length).split('/').filter(Boolean)) {
			acc = `${acc}/${seg}`;
			out.push({ label: seg, path: acc });
		}
		if (openFile) {
			out.push({ label: openFile.split('/').pop() ?? openFile, path: openFile });
		}
		return out;
	});
	const CRUMB_COLLAPSE_OVER = 4;
	let crumbsExpanded = $state(false);
	$effect(() => {
		void crumbBase;
		crumbsExpanded = false;
	});
	// null = 畳んだ中間(…)
	const visibleCrumbs = $derived(
		!crumbsExpanded && crumbs.length > CRUMB_COLLAPSE_OVER
			? [crumbs[0], null, ...crumbs.slice(-2)]
			: crumbs
	);

	const errorMessage = (err: unknown): string => {
		if (err instanceof SandboxFilesError) {
			switch (err.status) {
				case 507:
					return $i18n.t(
						'Your workspace is full. Empty the trash (/workspace/trash/) to free up space — deleting elsewhere only moves files there.'
					);
				case 413: {
					// detail.limit = 効いた上限(アップロードは sandbox の上限、保存はテキストの上限)
					const limit = (err.detail as { limit?: number | null } | null)?.limit ?? textLimit;
					return $i18n.t('The file is too large (limit {{SIZE}}).', {
						SIZE: formatFileSize(limit)
					});
				}
				case 403:
					return err.message === 'file upload is not permitted'
						? $i18n.t('You do not have permission to upload files.')
						: $i18n.t('This location is read-only.');
				case 404:
					return $i18n.t('Not found.');
				case 409:
					return $i18n.t('A file or folder with that name already exists.');
				case 0:
					return $i18n.t('Could not reach the server.');
				default:
					return err.message;
			}
		}
		return `${err}`;
	};

	// ---- 場所・URL ----
	const navigate = (nextDir: string, nextFile: string | null = null) => {
		const sp = new URLSearchParams();
		sp.set('path', nextDir);
		if (nextFile) {
			sp.set('file', nextFile);
		}
		goto(`?${sp}`, { keepFocus: true, noScroll: true });
	};

	const applyLocation = async (pathParam: string | null, fileParam: string | null) => {
		let nextDir = pathParam || WORKSPACE;
		if (fileParam && !pathParam) {
			nextDir = parentOf(fileParam);
		}
		// 見えない場所(非メンバーのグループ等)と存在しない場所は区別せず、同じ案内で /workspace へ。
		// URL も実際に出す場所へ置き換える(履歴は積まない)
		if (!placeOf(nextDir) || (fileParam && !placeOf(fileParam))) {
			// 場所一覧が取れていない(placesError)ときは何もしない — 置き換えても同じ判定でループする
			if (!placeOf(WORKSPACE)) {
				return;
			}
			toast.error($i18n.t('This location cannot be opened.'));
			goto(`?${new URLSearchParams({ path: WORKSPACE })}`, {
				replaceState: true,
				keepFocus: true,
				noScroll: true
			});
			return;
		}
		const wasFile = openFile;
		openFile = fileParam || null;
		// ファイルを閉じて一覧へ戻ったときも読み直す(保存でサイズ・更新日時が変わっている)
		if (nextDir !== dir || !listing || (wasFile && !openFile)) {
			dir = nextDir;
			await refresh();
		}
	};

	$effect(() => {
		const sp = $page.url.searchParams;
		const p = sp.get('path');
		const f = sp.get('file');
		if (!placesLoaded) {
			return;
		}
		untrack(() => applyLocation(p, f));
	});

	beforeNavigate(({ cancel }) => {
		if (skipDirtyCheck) {
			skipDirtyCheck = false;
			return;
		}
		if (openFile && fileDirty && !window.confirm($i18n.t('Discard unsaved changes?'))) {
			cancel();
		}
	});

	// ---- 読み込み ----
	const refreshUsage = async () => {
		if (!place || place.kind !== 'workspace') {
			return;
		}
		usage = await getSandboxUsage().catch(() => usage);
	};

	const refresh = async () => {
		loading = true;
		listError = '';
		const target = dir;
		try {
			const res = await listSandboxDir(target);
			if (target === dir) {
				listing = res;
			}
		} catch (err) {
			if (target === dir) {
				listing = null;
				listError =
					err instanceof SandboxFilesError && err.status === 404 && dir === place?.path
						? $i18n.t('This location is not available on this server.')
						: errorMessage(err);
			}
		}
		loading = false;
		refreshUsage();
	};

	onMount(async () => {
		try {
			const res = await getSandboxPlaces();
			places = res.places;
			textLimit = res.text_limit_bytes;
			uploadLimit = res.upload_limit_bytes;
			canUpload = res.can_upload;
		} catch (err) {
			placesError =
				err instanceof SandboxFilesError && err.status === 404
					? $i18n.t('The sandbox is not available on this server.')
					: errorMessage(err);
		}
		placesLoaded = true;
	});

	// ---- 表示の補助 ----
	const expiresAt = (e: SandboxEntry) => {
		const ttl = listing?.cache_ttl_days ?? 0;
		if (!ttl || !e.mtime || !EXPIRING_DIRS.includes(parentOf(e.path))) {
			return null;
		}
		return Math.max(e.mtime, e.ctime ?? 0) + ttl * 86400;
	};

	const expiryLabel = (ts: number) => {
		const days = Math.ceil((ts * 1000 - Date.now()) / 86400000);
		if (days <= 0) {
			return $i18n.t('Expires soon');
		}
		return $i18n.t('Expires {{DATE}} ({{COUNT}} days left)', {
			DATE: dayjs(ts * 1000).format('YYYY-MM-DD'),
			COUNT: days
		});
	};

	// 「1.0 MB · 2026-10-07 16:34」(区切りは前後に空白。テンプレートの改行に頼らない)
	const entryMeta = (e: SandboxEntry) =>
		[e.type === 'file' ? formatFileSize(e.size) : null, formatMtime(e.mtime)]
			.filter(Boolean)
			.join(' · ');

	const entryIcon = (e: SandboxEntry) => {
		if (e.type === 'dir') {
			return 'i-lucide-folder';
		}
		if (e.type === 'symlink') {
			return 'i-lucide-link';
		}
		if (/\.(md|markdown|txt|csv|tsv|json|ya?ml|toml|py|sh|html?|css|js|ts|sql|r)$/i.test(e.name)) {
			return 'i-lucide-file-text';
		}
		if (/\.(png|jpe?g|gif|webp|svg|bmp|tiff?)$/i.test(e.name)) {
			return 'i-lucide-file-image';
		}
		return 'i-lucide-file';
	};

	const formatMtime = (mtime: number | null) =>
		mtime ? dayjs(mtime * 1000).format('YYYY-MM-DD HH:mm') : '';

	const usageLabel = $derived(
		usage
			? `${(usage.used_bytes / 1024 ** 3).toFixed(1)} / ${usage.quota_gb} GB`
			: ''
	);

	const openEntry = (e: SandboxEntry) => {
		if (e.type === 'dir') {
			navigate(e.path);
		} else if (e.type === 'file') {
			navigate(dir, e.path);
		} else {
			toast.info($i18n.t('Symbolic links are not followed here.'));
		}
	};

	// ---- 名前の入力(新規フォルダ・新規ファイル・改名) ----
	let nameDialog = $state<{ mode: 'mkdir' | 'newfile' | 'rename'; entry?: SandboxEntry } | null>(
		null
	);
	let showNameDialog = $state(false);
	let nameValue = $state('');

	const openNameDialog = (mode: 'mkdir' | 'newfile' | 'rename', entry?: SandboxEntry) => {
		nameDialog = { mode, entry };
		nameValue = entry?.name ?? '';
		showNameDialog = true;
	};

	// 入力は今のディレクトリからの相対(`a/b` も可)か、`/` 始まりの絶対パス(改名での移動)
	const resolveInput = (value: string): string | null => {
		const v = value.trim();
		if (!v) {
			return null;
		}
		const abs = v.startsWith('/') ? v : `${dir}/${v}`;
		const segs = abs.split('/').filter(Boolean);
		if (segs.some((s) => s === '..' || s === '.' || s.startsWith('.'))) {
			toast.error($i18n.t('Names starting with "." cannot be used here.'));
			return null;
		}
		return `/${segs.join('/')}`;
	};

	const submitName = async () => {
		const d = nameDialog;
		const target = resolveInput(nameValue);
		if (!d || !target) {
			return;
		}
		busy = true;
		try {
			if (d.mode === 'mkdir') {
				await makeSandboxDir(target);
			} else if (d.mode === 'newfile') {
				await writeSandboxFile(target, '', { exclusive: true });
				navigate(parentOf(target), target);
			} else if (d.entry && target !== d.entry.path) {
				await renameSandboxPath(d.entry.path, target);
			}
			await refresh();
		} catch (err) {
			toast.error(errorMessage(err));
		}
		busy = false;
	};

	// ---- 削除 ----
	// /workspace の中(trash/ の外)は trash/ の直下へ移す(選び間違いを取り返せるように。llens の
	// 運用と同じ)。trash/ の中と /ext は完全に消す
	let deleteTarget = $state<SandboxEntry | null>(null);
	let showDeleteConfirm = $state(false);
	const movesToTrash = (e: SandboxEntry | null) =>
		!!e && inside(e.path, WORKSPACE) && !inside(e.path, TRASH);

	// 同名があれば末尾に -2, -3…(拡張子があればその前: memo.txt → memo-2.txt)
	const numberedName = (name: string, n: number) => {
		const dot = name.lastIndexOf('.');
		return dot > 0 ? `${name.slice(0, dot)}-${n}${name.slice(dot)}` : `${name}-${n}`;
	};

	const moveToTrash = async (e: SandboxEntry) => {
		const trash = await listSandboxDir(TRASH);
		if (!trash.exists) {
			await makeSandboxDir(TRASH);
		}
		const taken = new Set(trash.entries.map((x) => x.name));
		for (let n = 1; n < 1000; n++) {
			const name = n === 1 ? e.name : numberedName(e.name, n);
			if (taken.has(name)) {
				continue;
			}
			try {
				await renameSandboxPath(e.path, `${TRASH}/${name}`);
				return name;
			} catch (err) {
				// 一覧の後に同名ができた(sandbox の実行・別のタブ)— 次の番号へ
				if (err instanceof SandboxFilesError && err.status === 409) {
					taken.add(name);
					continue;
				}
				throw err;
			}
		}
		throw new Error($i18n.t('Could not find a free name in the trash.'));
	};

	const confirmDelete = async () => {
		const target = deleteTarget;
		if (!target) {
			return;
		}
		busy = true;
		try {
			if (movesToTrash(target)) {
				const name = await moveToTrash(target);
				toast.success(
					$i18n.t('Moved {{NAME}} to the trash', { NAME: `${TRASH}/${name}` })
				);
			} else {
				await deleteSandboxPath(target.path);
				toast.success($i18n.t('Deleted {{NAME}}', { NAME: target.name }));
			}
			await refresh();
		} catch (err) {
			toast.error(errorMessage(err));
		}
		busy = false;
	};

	// ---- アップロード ----
	let fileInput = $state<HTMLInputElement | null>(null);
	let uploading = $state(false);
	let dragOver = $state(false);
	let pendingUploads = $state<File[]>([]);
	let conflictNames = $state<string[]>([]);
	let showOverwriteConfirm = $state(false);

	const doUpload = async (files: File[], overwrite: boolean) => {
		if (files.length === 0) {
			return;
		}
		uploading = true;
		let ok = 0;
		for (const f of files) {
			if (uploadLimit !== null && f.size > uploadLimit) {
				toast.error(
					`${f.name}: ${$i18n.t('The file is too large (limit {{SIZE}}).', {
						SIZE: formatFileSize(uploadLimit)
					})}`
				);
				continue;
			}
			try {
				await uploadSandboxFile(dir, f, overwrite);
				ok += 1;
			} catch (err) {
				toast.error(`${f.name}: ${errorMessage(err)}`);
			}
		}
		uploading = false;
		if (ok > 0) {
			toast.success($i18n.t('Uploaded {{COUNT}} file(s)', { COUNT: ok }));
		}
		await refresh();
	};

	const startUpload = (files: File[]) => {
		if (!rw || !canUpload || files.length === 0) {
			return;
		}
		const existing = new Set((listing?.entries ?? []).map((e) => e.name));
		const conflicts = files.filter((f) => existing.has(f.name));
		if (conflicts.length === 0) {
			doUpload(files, false);
			return;
		}
		pendingUploads = files;
		conflictNames = conflicts.map((f) => f.name);
		showOverwriteConfirm = true;
	};
</script>

<input
	bind:this={fileInput}
	type="file"
	multiple
	hidden
	onchange={(e) => {
		startUpload(Array.from(e.currentTarget?.files ?? []));
		if (fileInput) {
			fileInput.value = '';
		}
	}}
/>

<ConfirmDialog
	bind:show={showNameDialog}
	title={nameDialog?.mode === 'mkdir'
		? $i18n.t('New folder')
		: nameDialog?.mode === 'newfile'
			? $i18n.t('New file')
			: $i18n.t('Rename')}
	confirmLabel={nameDialog?.mode === 'rename' ? $i18n.t('Rename') : $i18n.t('Create')}
	onConfirm={submitName}
>
	<div class="text-sm text-content-muted flex flex-col gap-2">
		<!-- svelte-ignore a11y_autofocus -->
		<input
			class="w-full rounded-lg px-3 py-2 text-sm text-content bg-surface-muted outline-hidden"
			bind:value={nameValue}
			autofocus
			onkeydown={(e) => {
				// IME の変換確定の Enter で ConfirmDialog(window の keydown)が確定しないように
				if (e.isComposing || e.keyCode === 229) {
					e.stopPropagation();
				}
			}}
			placeholder={nameDialog?.mode === 'mkdir' ? 'folder' : 'notes.md'}
		/>
		<div class="text-xs text-content-subtle">
			{nameDialog?.mode === 'rename'
				? $i18n.t('A name in this folder, or a full path such as {{PATH}} to move it.', {
						PATH: `${place?.path ?? WORKSPACE}/…`
					})
				: $i18n.t('Created in {{PATH}}', { PATH: dir })}
		</div>
	</div>
</ConfirmDialog>

<ConfirmDialog
	bind:show={showDeleteConfirm}
	title={movesToTrash(deleteTarget)
		? $i18n.t('Move {{NAME}} to the trash?', { NAME: deleteTarget?.name ?? '' })
		: inside(deleteTarget?.path ?? '', TRASH)
			? $i18n.t('Delete {{NAME}} permanently?', { NAME: deleteTarget?.name ?? '' })
			: $i18n.t('Delete {{NAME}}?', { NAME: deleteTarget?.name ?? '' })}
	message={movesToTrash(deleteTarget)
		? listing?.cache_ttl_days
			? $i18n.t(
					'It will be moved to /workspace/trash/ and deleted automatically {{COUNT}} days later. Until then you can move it back.',
					{ COUNT: listing.cache_ttl_days }
				)
			: $i18n.t('It will be moved to /workspace/trash/. You can move it back from there.')
		: deleteTarget?.type === 'dir'
			? $i18n.t('The folder and everything inside it will be deleted. This cannot be undone.')
			: $i18n.t('This cannot be undone.')}
	confirmLabel={movesToTrash(deleteTarget) ? $i18n.t('Move to trash') : $i18n.t('Delete')}
	onConfirm={confirmDelete}
/>

<ConfirmDialog
	bind:show={showOverwriteConfirm}
	title={$i18n.t('Overwrite existing files?')}
	message={$i18n.t('These files already exist in this folder: {{NAMES}}', {
		NAMES: conflictNames.join(', ')
	})}
	cancelLabel={$i18n.t('Skip existing')}
	confirmLabel={$i18n.t('Overwrite')}
	onConfirm={() => doUpload(pendingUploads, true)}
	onCancel={() =>
		doUpload(
			pendingUploads.filter((f) => !conflictNames.includes(f.name)),
			false
		)}
/>

<div class="flex flex-col gap-1 pt-1.5 h-full min-h-0">
	<div class="flex flex-wrap items-center gap-x-3 gap-y-1 px-0.5">
		<div class="text-xl font-medium">{$i18n.t('Sandbox')}</div>
		{#if usage && place?.kind === 'workspace'}
			<Tooltip
				content={$i18n.t('Workspace usage (quota). Space is freed when items are deleted from the trash (/workspace/trash/).')}
			>
				<div class="flex items-center gap-1.5 text-xs text-content-muted">
					<UsageGauge current={usage.used_bytes} total={usage.total_bytes} class="size-4" />
					<span>{usageLabel}</span>
				</div>
			</Tooltip>
		{/if}
	</div>

	{#if !placesLoaded}
		<div class="flex justify-center py-10"><Spinner className="size-5" /></div>
	{:else if placesError}
		<div class="text-sm text-content-subtle py-10 text-center">{placesError}</div>
	{:else}
		{#if !openFile}
			<!-- 場所の切り替え + 操作。狭幅では 2 行に折り返す -->
			<div class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-2">
				<Dropdown bind:show={showPlaces}>
					<span
						class="flex items-center gap-1.5 min-w-0 max-w-[calc(100vw-2rem)] sm:max-w-md px-2 py-1 rounded-lg bg-surface-form hover:bg-surface-hover text-sm"
					>
						<span class="sr-only">{$i18n.t('Location')}:</span>
						<span class="{placeIcon(place)} size-4 block shrink-0 text-content-muted"></span>
						<span class="font-mono truncate">{place?.path ?? WORKSPACE}</span>
						{#if readOnly}
							<span
								class="i-lucide-lock size-3.5 block shrink-0 text-content-muted"
								title={$i18n.t('Read-only')}
							></span>
							<span class="sr-only">({$i18n.t('Read-only')})</span>
						{/if}
						<span class="i-lucide-chevron-down size-3.5 block shrink-0 text-content-subtle"></span>
					</span>
					{#snippet content()}
						<DropdownContent
							className="min-w-[14rem] max-w-[calc(100vw-2rem)] sm:max-w-md rounded-xl px-1 py-1.5 border border-line z-50 bg-surface-raised text-content shadow-sm"
							sideOffset={4}
							align="start"
						>
							{#each places as p (p.path)}
								<DropdownMenu.Item
									class="flex gap-2 items-center px-3 py-2 text-sm cursor-pointer hover:bg-surface-hover rounded-md"
									onSelect={() => navigate(p.path)}
								>
									<span class="{placeIcon(p)} size-4 block shrink-0 text-content-muted"></span>
									<span class="font-mono flex-1 min-w-0 break-all">{p.path}</span>
									{#if !p.rw}
										<span
											class="i-lucide-lock size-3.5 block shrink-0 text-content-muted"
											title={$i18n.t('Read-only')}
										></span>
										<span class="sr-only">({$i18n.t('Read-only')})</span>
									{/if}
									<span
										class="i-lucide-check size-4 block shrink-0 {p.path === place?.path
											? ''
											: 'invisible'}"
									></span>
								</DropdownMenu.Item>
							{/each}
						</DropdownContent>
					{/snippet}
				</Dropdown>
				<div class="flex items-center gap-0.5 ml-auto">
					{#if rw}
						{#if canUpload}
							<button
								class="whitespace-nowrap inline-flex items-center py-0.5 pl-2 pr-3 mr-1 rounded-full bg-primary text-primary-content font-medium text-sm hover:bg-primary-hover disabled:opacity-50"
								type="button"
								disabled={uploading}
								onclick={() => fileInput?.click()}
							>
								{#if uploading}
									<Spinner className="size-3.5 mr-1" />
								{:else}
									<span class="i-lucide-upload size-3.5 block mr-1"></span>
								{/if}
								{$i18n.t('Upload')}
							</button>
						{/if}
						<Tooltip content={$i18n.t('New folder')}>
							<button
								class="p-1.5 rounded-lg text-content-muted hover:bg-surface-hover"
								type="button"
								aria-label={$i18n.t('New folder')}
								disabled={busy}
								onclick={() => openNameDialog('mkdir')}
							>
								<span class="i-lucide-folder-plus size-4 block"></span>
							</button>
						</Tooltip>
						<Tooltip content={$i18n.t('New file')}>
							<button
								class="p-1.5 rounded-lg text-content-muted hover:bg-surface-hover"
								type="button"
								aria-label={$i18n.t('New file')}
								disabled={busy}
								onclick={() => openNameDialog('newfile')}
							>
								<span class="i-lucide-file-plus size-4 block"></span>
							</button>
						</Tooltip>
					{/if}
					<Tooltip content={$i18n.t('Refresh')}>
						<button
							class="p-1.5 rounded-lg text-content-muted hover:bg-surface-hover"
							type="button"
							aria-label={$i18n.t('Refresh')}
							onclick={() => refresh()}
						>
							<span class="i-lucide-refresh-cw size-4 block {loading ? 'animate-spin' : ''}"></span>
						</button>
					</Tooltip>
				</div>
			</div>
		{/if}

		<!-- パンくず: 実パスに見える形。先頭は場所の root を 1 要素で、末尾(今いる所)はクリック不可 -->
		<nav
			class="flex flex-wrap items-baseline px-0.5 font-mono text-[0.8125rem] leading-6"
			aria-label={$i18n.t('Breadcrumb')}
		>
			{#each visibleCrumbs as c, i}
				{#if i > 0}
					<span class="px-1 text-content-subtle select-none" aria-hidden="true">/</span>
				{/if}
				{#if c === null}
					<button
						class="px-0.5 rounded-md text-content-muted hover:text-content hover:bg-surface-hover"
						type="button"
						title={crumbBase}
						aria-label={$i18n.t('Show full path')}
						onclick={() => (crumbsExpanded = true)}>…</button
					>
				{:else if i === visibleCrumbs.length - 1}
					<span class="font-medium text-content break-all" aria-current="page">{c.label}</span>
				{:else}
					<button
						class="text-left break-all rounded-md text-content-muted hover:text-content hover:underline"
						type="button"
						onclick={() => navigate(c.path)}>{c.label}</button
					>
				{/if}
			{/each}
		</nav>

		{#if openFile}
			{#key openFile}
				<SandboxFileView
					path={openFile}
					expiresAt={(() => {
						const e = listing?.entries.find((x) => x.path === openFile);
						return e ? expiresAt(e) : null;
					})()}
					{errorMessage}
					onDirtyChange={(d) => (fileDirty = d)}
					onClose={(force) => {
						if (force) {
							skipDirtyCheck = true;
						}
						navigate(dir);
					}}
				/>
			{/key}
		{:else}
		<!-- ドロップ先は一覧の下の空きまで含めた領域(flex-1、内容より短くはならない)。枠線は一覧(ul)と
		     空・エラー表示の側に持つ — 領域に枠を付けると最後の行が画面下まで伸びて見える -->
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<div
			class="mt-1 flex-1 rounded-lg {dragOver ? 'ring-2 ring-inset ring-primary bg-surface-hover' : ''}"
			ondragover={(e) => {
				if (rw && canUpload && e.dataTransfer?.types.includes('Files')) {
					e.preventDefault();
					dragOver = true;
				}
			}}
			ondragleave={() => (dragOver = false)}
			ondrop={(e) => {
				dragOver = false;
				if (!rw || !canUpload) {
					return;
				}
				e.preventDefault();
				startUpload(Array.from(e.dataTransfer?.files ?? []));
			}}
		>
			{#if loading && !listing}
				<div class="flex justify-center py-10 rounded-lg border border-line"><Spinner className="size-5" /></div>
			{:else if listError}
				<div class="text-sm text-content-subtle py-10 text-center rounded-lg border border-line">
					{listError}
				</div>
			{:else if listing && !listing.exists}
				<div
					class="text-sm text-content-subtle py-10 text-center flex flex-col items-center gap-2 rounded-lg border border-line"
				>
					{$i18n.t('This folder does not exist.')}
					{#if place}
						<button
							class="text-sm font-mono underline text-content-muted hover:text-content"
							type="button"
							onclick={() => navigate(place.path)}>{place.path}</button
						>
					{/if}
				</div>
			{:else if listing && entries.length === 0}
				<div class="text-sm text-content-subtle py-10 text-center rounded-lg border border-line">
					{rw && canUpload
						? $i18n.t('This folder is empty. Drop files here to upload.')
						: $i18n.t('This folder is empty.')}
				</div>
			{:else if listing}
				<ul class="rounded-lg border border-line divide-y divide-line overflow-hidden">
					{#each entries as e (e.path)}
						{@const exp = expiresAt(e)}
						<li class="group flex items-center gap-2 px-2 py-1.5 hover:bg-surface-hover">
							<button
								class="flex-1 min-w-0 flex items-center gap-2 text-left"
								type="button"
								onclick={() => openEntry(e)}
							>
								<span
									class="{entryIcon(e)} size-4 block shrink-0 {e.type === 'dir'
										? 'text-amber-500 dark:text-amber-400'
										: 'text-content-muted'}"
								></span>
								<span class="min-w-0 flex-1">
									<span class="block text-sm truncate {e.type === 'symlink' ? 'italic' : ''}"
										>{e.name}</span
									>
									<span class="block text-xs text-content-subtle truncate">
										{entryMeta(e)}{#if e.path === TRASH && e.type === 'dir'}{' · '}{listing.cache_ttl_days
												? $i18n.t('Deleted items, removed {{COUNT}} days after they are moved here', {
														COUNT: listing.cache_ttl_days
													})
												: $i18n.t('Deleted items')}{/if}{#if exp}{' · '}<span
												class={exp * 1000 - Date.now() < 3 * 86400000
													? 'text-amber-600 dark:text-amber-400'
													: ''}>{expiryLabel(exp)}</span
											>
										{/if}
									</span>
								</span>
							</button>
							<div
								class="flex items-center gap-0.5 shrink-0 sm:opacity-0 sm:group-hover:opacity-100 sm:focus-within:opacity-100"
							>
								{#if e.type === 'file'}
									<Tooltip content={$i18n.t('Download')}>
										<a
											class="p-1.5 rounded-lg text-content-muted hover:bg-surface-raised flex"
											href={sandboxDownloadUrl(e.path)}
											download={e.name}
											aria-label={$i18n.t('Download')}
										>
											<span class="i-lucide-download size-4 block"></span>
										</a>
									</Tooltip>
								{/if}
								{#if rw}
									<Tooltip content={$i18n.t('Rename')}>
										<button
											class="p-1.5 rounded-lg text-content-muted hover:bg-surface-raised"
											type="button"
											aria-label={$i18n.t('Rename')}
											onclick={() => openNameDialog('rename', e)}
										>
											<span class="i-lucide-pencil size-4 block"></span>
										</button>
									</Tooltip>
									<Tooltip content={$i18n.t('Delete')}>
										<button
											class="p-1.5 rounded-lg text-content-muted hover:bg-surface-raised hover:text-red-500"
											type="button"
											aria-label={$i18n.t('Delete')}
											onclick={() => {
												deleteTarget = e;
												showDeleteConfirm = true;
											}}
										>
											<span class="i-lucide-trash-2 size-4 block"></span>
										</button>
									</Tooltip>
								{/if}
							</div>
						</li>
					{/each}
				</ul>
				{#if listing.truncated}
					<div class="text-xs text-content-subtle px-2 py-1.5">
						{$i18n.t('Too many entries — only the first part is shown.')}
					</div>
				{/if}
			{/if}
		</div>

		{#if place?.kind === 'workspace' && listing?.cache_ttl_days && CACHE_DIRS.some((d) => inside(dir, d))}
			<div class="text-xs text-content-subtle px-0.5 pb-1">
				ⓘ {$i18n.t(
					'Files here are copies of chat attachments and outputs. Each item directly in this folder (a file or a folder) is deleted with everything inside {{COUNT}} days after it was put here or last changed. The originals stay in Files.',
					{ COUNT: listing.cache_ttl_days }
				)}
			</div>
		{:else if place?.kind === 'workspace' && inside(dir, TRASH)}
			<div class="text-xs text-content-subtle px-0.5 pb-1">
				ⓘ {listing?.cache_ttl_days
					? $i18n.t(
							'Deleting in /workspace moves items here. Each item directly in this folder is deleted with everything inside {{COUNT}} days after it was moved here. Deleting here removes it permanently.',
							{ COUNT: listing.cache_ttl_days }
						)
					: $i18n.t(
							'Deleting in /workspace moves items here. Deleting here removes it permanently.'
						)}
			</div>
		{/if}
		{/if}
	{/if}
</div>
