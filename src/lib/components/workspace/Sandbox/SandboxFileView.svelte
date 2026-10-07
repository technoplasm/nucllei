<script lang="ts">
	// #150: sandbox のファイル 1 つの表示・編集。平文(UTF-8・NUL 無し・テキストの上限以内 — 判定は
	// サーバー)は CodeEditor で編集でき(書ける場所のときだけ)、md はプレビューも出す。
	// 保存は開いたときの mtime を expected_mtime に付け、外(jail の実行・別のタブ)で
	// 変わっていたら 409 →「外で変更された」モーダルで 再読み込み / 上書き / 編集を続ける を選ばせる。
	import { getContext, onDestroy, onMount, untrack } from 'svelte';
	import { toast } from 'svelte-sonner';
	import dayjs from 'dayjs';

	import { LanguageDescription } from '@codemirror/language';
	import { languages } from '@codemirror/language-data';

	import {
		readSandboxFile,
		writeSandboxFile,
		sandboxDownloadUrl,
		SandboxFilesError,
		type SandboxFileContent
	} from '$lib/apis/sandbox';
	import { formatFileSize } from '$lib/utils';

	import CodeEditor from '$lib/components/common/CodeEditor.svelte';
	import Modal from '$lib/components/common/Modal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Markdown from '$lib/components/chat/Messages/Markdown.svelte';

	const i18n = getContext('i18n');

	let {
		path,
		expiresAt = null,
		onClose,
		onDirtyChange = () => {},
		errorMessage
	}: {
		path: string;
		/** imports/ exports/ の残り期限(unix 秒)。無ければ null */
		expiresAt?: number | null;
		/** 一覧へ戻る。force = 未保存の確認をしない(利用者が破棄を選んだ) */
		onClose: (force?: boolean) => void;
		onDirtyChange?: (dirty: boolean) => void;
		/** API の失敗を利用者向けの文に(一覧と同じ言い分け) */
		errorMessage: (err: unknown) => string;
	} = $props();

	let loading = $state(true);
	let file = $state<SandboxFileContent | null>(null);
	let original = $state('');
	let draft = $state('');
	let saving = $state(false);
	// CodeEditor は value の外からの差し替えに弱いので、再読み込みは key を変えて作り直す
	let editorKey = $state(0);
	let mode = $state<'edit' | 'preview'>('edit');

	let showConflict = $state(false);
	let conflictDeleted = $state(false);

	const name = $derived(path.split('/').pop() ?? path);
	const isMarkdown = $derived(/\.(md|markdown|mkd)$/i.test(name));
	const editable = $derived(!!file && file.rw && file.text !== null);
	const dirty = $derived(editable && draft !== original);
	// 拡張子から CodeMirror の言語(CodeEditor は alias 一致で引くので小文字の名前 = alias を渡す)
	const lang = $derived(
		LanguageDescription.matchFilename(languages, name)?.name.toLowerCase() ?? ''
	);

	// 親への通知は依存を dirty だけに絞る(親 handler の読み書きを依存に入れない —
	// _memos/dev-environment.md の Svelte 5 罠 (7))
	$effect(() => {
		const d = dirty;
		untrack(() => onDirtyChange(d));
	});

	const load = async () => {
		loading = true;
		try {
			const res = await readSandboxFile(path);
			file = res;
			original = res.text ?? '';
			draft = original;
			editorKey += 1;
			// 書けない md はプレビューから、書ける md は編集から
			mode = isMarkdown && !(res.rw && res.text !== null) ? 'preview' : 'edit';
		} catch (err) {
			toast.error(errorMessage(err));
			file = null;
		}
		loading = false;
	};

	const save = async (force = false) => {
		if (!file || !editable || saving) {
			return;
		}
		saving = true;
		try {
			const res = await writeSandboxFile(path, draft, {
				expectedMtime: force ? null : file.mtime
			});
			file = { ...file, mtime: res.mtime, size: res.size };
			original = draft;
			showConflict = false;
			toast.success($i18n.t('Saved'));
		} catch (err) {
			if (err instanceof SandboxFilesError && err.status === 409) {
				conflictDeleted = (err.detail as { message?: string } | null)?.message === 'deleted';
				showConflict = true;
			} else {
				toast.error(errorMessage(err));
			}
		}
		saving = false;
	};

	const beforeUnload = (e: BeforeUnloadEvent) => {
		if (dirty) {
			e.preventDefault();
		}
	};

	onMount(() => {
		load();
		window.addEventListener('beforeunload', beforeUnload);
	});

	onDestroy(() => {
		window.removeEventListener('beforeunload', beforeUnload);
	});

	const formatMtime = (mtime: number | null | undefined) =>
		mtime ? dayjs(mtime * 1000).format('YYYY-MM-DD HH:mm') : '-';
</script>

<div class="flex flex-col h-full min-h-0">
	<div class="flex flex-wrap items-center gap-x-2 gap-y-1 py-1">
		<button
			class="shrink-0 p-1.5 rounded-lg text-content-muted hover:bg-surface-hover"
			type="button"
			aria-label={$i18n.t('Back')}
			onclick={() => onClose()}
		>
			<span class="i-lucide-arrow-left size-4 block"></span>
		</button>
		<!-- ファイル名・パスは親のパンくずが出す。ここは状態と属性だけ -->
		<div
			class="flex-1 min-w-fit flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-xs text-content-subtle"
		>
			{#if dirty}
				<span class="flex items-center gap-1 text-amber-600 dark:text-amber-400 shrink-0">
					<span class="size-1.5 rounded-full bg-amber-500"></span>
					{$i18n.t('Unsaved')}
				</span>
			{/if}
			{#if file && !file.rw}
				<span class="flex items-center gap-1 text-content-muted shrink-0">
					<span class="i-lucide-lock size-3 block"></span>
					{$i18n.t('Read-only')}
				</span>
			{/if}
			{#if file}
				<span class="whitespace-nowrap">{formatFileSize(file.size)} · {formatMtime(file.mtime)}</span>
			{/if}
			{#if expiresAt}
				<span class="whitespace-nowrap"
					>{$i18n.t('Expires {{DATE}}', {
						DATE: dayjs(expiresAt * 1000).format('YYYY-MM-DD')
					})}</span
				>
			{/if}
		</div>

		<div class="flex items-center gap-0.5 ml-auto">
			{#if isMarkdown && file?.text != null}
				<div class="flex items-center rounded-lg bg-surface-muted p-0.5 text-xs mr-1">
					<button
						class="px-2 py-0.5 rounded-md {mode === 'edit'
							? 'bg-surface-raised text-content'
							: 'text-content-muted'}"
						type="button"
						onclick={() => (mode = 'edit')}
						>{editable ? $i18n.t('Edit') : $i18n.t('Source')}</button
					>
					<button
						class="px-2 py-0.5 rounded-md {mode === 'preview'
							? 'bg-surface-raised text-content'
							: 'text-content-muted'}"
						type="button"
						onclick={() => (mode = 'preview')}>{$i18n.t('Preview')}</button
					>
				</div>
			{/if}
			<Tooltip content={$i18n.t('Reload')}>
				<button
					class="p-1.5 rounded-lg text-content-muted hover:bg-surface-hover"
					type="button"
					aria-label={$i18n.t('Reload')}
					onclick={() => load()}
				>
					<span class="i-lucide-refresh-cw size-4 block"></span>
				</button>
			</Tooltip>
			<Tooltip content={$i18n.t('Download')}>
				<a
					class="p-1.5 rounded-lg text-content-muted hover:bg-surface-hover"
					href={sandboxDownloadUrl(path)}
					download={name}
					aria-label={$i18n.t('Download')}
				>
					<span class="i-lucide-download size-4 block"></span>
				</a>
			</Tooltip>
			{#if editable}
				<button
					class="ml-1 inline-flex items-center gap-1 py-1 px-3 rounded-full bg-primary text-primary-content text-sm font-medium hover:bg-primary-hover disabled:opacity-50"
					type="button"
					disabled={!dirty || saving}
					onclick={() => save()}
				>
					{#if saving}
						<Spinner className="size-3.5" />
					{:else}
						<span class="i-lucide-save size-3.5 block"></span>
					{/if}
					{$i18n.t('Save')}
				</button>
			{/if}
		</div>
	</div>

	{#if loading && !file}
		<div class="flex justify-center py-10"><Spinner className="size-5" /></div>
	{:else if !file}
		<div class="text-sm text-content-subtle py-10 text-center">
			{$i18n.t('Could not open this file.')}
		</div>
	{:else if file.text === null}
		<div class="text-sm text-content-muted py-10 text-center flex flex-col items-center gap-3">
			<span class="i-lucide-file-x size-8 block text-content-subtle"></span>
			<div>
				{file.reason === 'too_large'
					? $i18n.t('This file is too large to open as text. Download it instead.')
					: $i18n.t('This file is not plain text (UTF-8). Download it instead.')}
			</div>
			<a
				class="inline-flex items-center gap-1 py-1 px-3 rounded-full bg-secondary text-secondary-content hover:bg-secondary-hover text-sm"
				href={sandboxDownloadUrl(path)}
				download={name}
			>
				<span class="i-lucide-download size-3.5 block"></span>
				{$i18n.t('Download')}
			</a>
		</div>
	{:else if isMarkdown && mode === 'preview'}
		<div class="flex-1 min-h-0 overflow-auto rounded-lg border border-line p-3 w-full min-w-full markdown-prose text-sm">
			<Markdown id={`sandbox-preview-${editorKey}`} content={draft} />
		</div>
	{:else if editable}
		<!-- 縦が狭い画面ではエディタを潰さず(min-h-96)ページスクロールに逃がす(SkillEditor と同じ) -->
		<div class="flex-1 min-h-96 rounded-lg border border-line overflow-auto">
			{#key editorKey}
				<CodeEditor
					id={`sandbox-file-${editorKey}`}
					{lang}
					value={original}
					onChange={(v) => {
						draft = v;
					}}
					onSave={() => {
						save();
					}}
				/>
			{/key}
		</div>
	{:else}
		<pre
			class="flex-1 min-h-0 overflow-auto text-xs whitespace-pre-wrap break-words bg-surface-muted rounded-lg p-3"><code
				>{file.text}</code
			></pre>
	{/if}
</div>

<Modal bind:show={showConflict} size="sm">
	<div class="px-4 py-3 flex flex-col gap-3">
		<div class="text-lg font-semibold text-content">
			{$i18n.t('Changed outside the editor')}
		</div>
		<div class="text-sm text-content-muted">
			{conflictDeleted
				? $i18n.t(
						'This file was deleted after you opened it (for example by a sandbox run). Save your version as a new file, or discard your edits?'
					)
				: $i18n.t(
						'This file was changed after you opened it (for example by a sandbox run). Reload the latest version (your unsaved edits will be lost), or overwrite it with your version?'
					)}
		</div>
		<div class="flex flex-wrap justify-end gap-1.5">
			<button
				class="px-3 py-1.5 rounded-lg text-sm text-content-muted hover:bg-surface-hover"
				type="button"
				onclick={() => (showConflict = false)}>{$i18n.t('Keep editing')}</button
			>
			<button
				class="px-3 py-1.5 rounded-lg text-sm bg-secondary text-secondary-content hover:bg-secondary-hover"
				type="button"
				onclick={() => {
					showConflict = false;
					if (conflictDeleted) {
						onClose(true);
					} else {
						load();
					}
				}}>{conflictDeleted ? $i18n.t('Discard') : $i18n.t('Reload')}</button
			>
			<button
				class="px-3 py-1.5 rounded-lg text-sm bg-primary text-primary-content hover:bg-primary-hover"
				type="button"
				onclick={() => save(true)}
				>{conflictDeleted ? $i18n.t('Save as new') : $i18n.t('Overwrite')}</button
			>
		</div>
	</div>
</Modal>
