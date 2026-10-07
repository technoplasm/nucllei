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

	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import UsageGauge from '$lib/components/common/UsageGauge.svelte';
	import SandboxFileView from './SandboxFileView.svelte';

	const i18n = getContext('i18n');

	const WORKSPACE = '/workspace';
	// imports/ exports/ は Files の写しで、最終更新から cache_ttl_days 日で消える(#149)
	const CACHE_DIRS = [`${WORKSPACE}/imports/`, `${WORKSPACE}/exports/`];

	let places = $state<SandboxPlace[]>([]);
	let placesLoaded = $state(false);
	let placesError = $state('');
	let writeLimit = $state(0);

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

	const placeLabel = (p: SandboxPlace) =>
		p.kind === 'workspace'
			? $i18n.t('My workspace')
			: p.kind === 'public'
				? $i18n.t('Public')
				: (p.path.split('/').pop() ?? p.path);

	const placeIcon = (p: SandboxPlace | null) =>
		p?.kind === 'public'
			? 'i-lucide-globe'
			: p?.kind === 'group'
				? 'i-lucide-users'
				: 'i-lucide-hard-drive';

	// パンくず: 場所の root から今のディレクトリまで
	const crumbs = $derived.by(() => {
		if (!place) {
			return [];
		}
		const out = [{ label: placeLabel(place), path: place.path }];
		const rest = dir.slice(place.path.length).split('/').filter(Boolean);
		let acc = place.path;
		for (const seg of rest) {
			acc = `${acc}/${seg}`;
			out.push({ label: seg, path: acc });
		}
		return out;
	});

	const errorMessage = (err: unknown): string => {
		if (err instanceof SandboxFilesError) {
			switch (err.status) {
				case 507:
					return $i18n.t(
						'Your workspace is full. Delete files you no longer need to free up space.'
					);
				case 413:
					return $i18n.t('The file is too large (limit {{SIZE}}).', {
						SIZE: formatFileSize(writeLimit)
					});
				case 403:
					return $i18n.t('This location is read-only.');
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

	const parentOf = (path: string) => path.slice(0, path.lastIndexOf('/')) || '/';

	const applyLocation = async (pathParam: string | null, fileParam: string | null) => {
		let nextDir = pathParam || WORKSPACE;
		if (fileParam && !pathParam) {
			nextDir = parentOf(fileParam);
		}
		if (!placeOf(nextDir)) {
			nextDir = WORKSPACE;
		}
		const wasFile = openFile;
		openFile = fileParam && placeOf(fileParam) ? fileParam : null;
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
			writeLimit = res.write_limit_bytes;
		} catch (err) {
			placesError =
				err instanceof SandboxFilesError && err.status === 404
					? $i18n.t('The sandbox is not available on this server.')
					: errorMessage(err);
		}
		placesLoaded = true;
	});

	// ---- 表示の補助 ----
	const isCached = (e: SandboxEntry) =>
		e.type === 'file' && CACHE_DIRS.some((d) => e.path.startsWith(d));

	const expiresAt = (e: SandboxEntry) => {
		const ttl = listing?.cache_ttl_days ?? 0;
		if (!ttl || !e.mtime || !isCached(e)) {
			return null;
		}
		return e.mtime + ttl * 86400;
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
	let deleteTarget = $state<SandboxEntry | null>(null);
	let showDeleteConfirm = $state(false);

	const confirmDelete = async () => {
		const target = deleteTarget;
		if (!target) {
			return;
		}
		busy = true;
		try {
			await deleteSandboxPath(target.path);
			toast.success($i18n.t('Deleted {{NAME}}', { NAME: target.name }));
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
			if (writeLimit && f.size > writeLimit) {
				toast.error(
					`${f.name}: ${$i18n.t('The file is too large (limit {{SIZE}}).', {
						SIZE: formatFileSize(writeLimit)
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
		if (!rw || files.length === 0) {
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
	title={$i18n.t('Delete {{NAME}}?', { NAME: deleteTarget?.name ?? '' })}
	message={deleteTarget?.type === 'dir'
		? $i18n.t('The folder and everything inside it will be deleted. This cannot be undone.')
		: $i18n.t('This cannot be undone.')}
	confirmLabel={$i18n.t('Delete')}
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

<div class="flex flex-col gap-1 mt-1.5 h-full min-h-0">
	<div class="flex flex-wrap items-center gap-x-3 gap-y-1 px-0.5">
		<div class="text-xl font-medium">{$i18n.t('Sandbox')}</div>
		{#if usage && place?.kind === 'workspace'}
			<Tooltip
				content={$i18n.t('Workspace usage (quota). Delete files you no longer need to free space.')}
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
	{:else if openFile}
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
		<!-- 場所 + 操作。狭幅では 2 行に折り返す -->
		<div class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-2">
			<div class="flex items-center gap-1.5 min-w-0">
				<span class="{placeIcon(place)} size-4 block shrink-0 text-content-muted"></span>
				<select
					class="bg-surface-form hover:bg-surface-hover max-w-[14rem] px-2 py-1 pr-6 rounded-xs text-sm outline-hidden"
					value={place?.path ?? WORKSPACE}
					aria-label={$i18n.t('Location')}
					onchange={(e) => navigate(e.currentTarget.value)}
				>
					{#each places as p (p.path)}
						<option value={p.path}
							>{placeLabel(p)}{p.rw ? '' : ` (${$i18n.t('Read-only')})`}</option
						>
					{/each}
				</select>
				{#if place && !rw && listing}
					<span
						class="text-[0.65rem] px-1.5 py-px rounded-full bg-surface-muted text-content-muted shrink-0"
						>{$i18n.t('Read-only')}</span
					>
				{/if}
			</div>

			<div class="flex items-center gap-0.5 ml-auto">
				{#if rw}
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

		<!-- パンくず -->
		<nav
			class="flex items-center gap-0.5 text-sm px-0.5 overflow-x-auto scrollbar-none whitespace-nowrap"
			aria-label={$i18n.t('Breadcrumb')}
		>
			{#each crumbs as c, i (c.path)}
				{#if i > 0}
					<span class="i-lucide-chevron-right size-3.5 block shrink-0 text-content-subtle"></span>
				{/if}
				{#if i === crumbs.length - 1}
					<span class="px-1 font-medium">{c.label}</span>
				{:else}
					<button
						class="px-1 rounded-md text-content-muted hover:text-content hover:bg-surface-hover"
						type="button"
						onclick={() => navigate(c.path)}>{c.label}</button
					>
				{/if}
			{/each}
		</nav>

		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<div
			class="mt-1 flex-1 min-h-0 rounded-lg border {dragOver
				? 'border-primary bg-surface-hover'
				: 'border-line'}"
			ondragover={(e) => {
				if (rw && e.dataTransfer?.types.includes('Files')) {
					e.preventDefault();
					dragOver = true;
				}
			}}
			ondragleave={() => (dragOver = false)}
			ondrop={(e) => {
				dragOver = false;
				if (!rw) {
					return;
				}
				e.preventDefault();
				startUpload(Array.from(e.dataTransfer?.files ?? []));
			}}
		>
			{#if loading && !listing}
				<div class="flex justify-center py-10"><Spinner className="size-5" /></div>
			{:else if listError}
				<div class="text-sm text-content-subtle py-10 text-center">{listError}</div>
			{:else if listing && !listing.exists}
				<div class="text-sm text-content-subtle py-10 text-center flex flex-col items-center gap-2">
					{$i18n.t('This folder does not exist.')}
					{#if place}
						<button
							class="text-sm underline text-content-muted hover:text-content"
							type="button"
							onclick={() => navigate(place.path)}>{placeLabel(place)}</button
						>
					{/if}
				</div>
			{:else if listing && listing.entries.length === 0}
				<div class="text-sm text-content-subtle py-10 text-center">
					{rw
						? $i18n.t('This folder is empty. Drop files here to upload.')
						: $i18n.t('This folder is empty.')}
				</div>
			{:else if listing}
				<ul class="divide-y divide-line">
					{#each listing.entries as e (e.path)}
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
										{#if e.type === 'file'}{formatFileSize(e.size)} ·
										{/if}{formatMtime(e.mtime)}
										{#if exp}
											· <span
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

		{#if place?.kind === 'workspace' && listing?.cache_ttl_days && (dir === `${WORKSPACE}/imports` || dir === `${WORKSPACE}/exports` || dir.startsWith(`${WORKSPACE}/imports/`) || dir.startsWith(`${WORKSPACE}/exports/`))}
			<div class="text-xs text-content-subtle px-0.5 pb-1">
				ⓘ {$i18n.t(
					'Files here are copies of chat attachments and outputs, and are deleted {{COUNT}} days after their last change. The originals stay in Files.',
					{ COUNT: listing.cache_ttl_days }
				)}
			</div>
		{/if}
	{/if}
</div>
