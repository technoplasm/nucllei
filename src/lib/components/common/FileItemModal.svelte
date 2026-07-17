<script lang="ts">
	import { getContext } from 'svelte';
	import { formatFileSize } from '$lib/utils';
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	const i18n = getContext('i18n');

	import Modal from './Modal.svelte';
	import Markdown from '../chat/Messages/Markdown.svelte';
	import Tooltip from './Tooltip.svelte';

	import { getFileById } from '$lib/apis/files';

	// llens(#69): /files 管理ページ用の拡張(すべて省略可 — チャット側の既存利用は不変)。
	// download=true でダウンロードリンク、onDelete/onShare はフッターのボタンから callback。
	let {
		item,
		show = $bindable(false),
		download = false,
		onDelete = null,
		onShare = null
	}: {
		item: any;
		show?: boolean;
		download?: boolean;
		onDelete?: null | (() => void);
		onShare?: null | (() => void);
	} = $props();

	// llens: 既定タブは preview(PDF=原本、テキスト系=md レンダリング)。content は md ソース。
	let selectedTab = $state<'content' | 'preview'>('preview');

	// llens(#69): 画像 / PDF は元データをそのままプレビュー(content 取得は同一 URL)。
	const fileId = $derived(item?.file?.id ?? item?.id);
	const contentType = $derived(item?.file?.meta?.content_type ?? item?.meta?.content_type ?? '');
	const isImage = $derived(contentType.startsWith('image/'));
	const isPdf = $derived(contentType === 'application/pdf');

	// llens: チャット側の item.file はアップロード時のスナップショットで古いことがある
	// (後から抽出が走った・Attachment 設定が変わった等)。開くたびに API から取り直して
	// data(抽出 content / status)を最新化する。取得失敗時はスナップショットのまま。
	let freshFile = $state<{ data?: { content?: string; status?: string } } | null>(null);
	$effect(() => {
		if (!show) {
			return;
		}
		selectedTab = 'preview';
		freshFile = null;
		if (fileId) {
			getFileById(fileId)
				.then((f) => (freshFile = f))
				.catch(() => {});
		}
	});

	// llens(#52): 添付ファイルは「モデルにどう見えるか(= 抽出された content)」を見せる。
	// content は md 前提。コンテンツ=md のソース(code block)、プレビュー=md をレンダリング。
	// 状態は attachment が制御(success/failed)。未抽出(none)= status も content も無い。
	const data = $derived(freshFile?.data ?? item?.file?.data ?? {});
	const content = $derived(data?.content ?? '');
	const status = $derived(data?.status); // 'success' | 'failed' | undefined
	const extraction = $derived(
		status === 'failed'
			? 'failed'
			: content.trim() === '' && status !== 'success'
				? 'none'
				: 'success'
	);

	// file の ACL 意味論: null=非公開 / read.public=全体公開 / それ以外=グループ共有
	const accessControl = $derived(item?.file?.access_control ?? null);
	const visibility = $derived(
		accessControl === null ? 'private' : accessControl?.read?.public ? 'public' : 'shared'
	);
</script>

<Modal bind:show size="lg">
	<div class="font-primary px-6 py-5 w-full flex flex-col justify-center text-content-muted">
		<div class="pb-2">
			<!-- llens: 縦スペース節約 — size • content-type と抽出バッジはファイル名と同じ行に -->
			<div class="flex items-start justify-between gap-3">
				<div class="flex items-baseline gap-2 min-w-0">
					<div class="font-medium text-lg text-content line-clamp-1">
						{item?.name ?? 'File'}
					</div>

					<div class="shrink-0 flex items-center gap-1.5 text-sm text-content-muted">
						{#if item?.size}
							<span>{formatFileSize(item.size)}</span>
						{/if}
						{#if (isImage || isPdf) && contentType}
							{#if item?.size}<span>•</span>{/if}
							<span>{contentType}</span>
						{/if}
						{#if !isImage}
							{#if extraction === 'failed'}
								<span class="text-red-500 font-medium">・{$i18n.t('Extraction failed')}</span>
							{:else if extraction === 'none'}
								<span class="text-content-subtle font-medium">・{$i18n.t('Not extracted')}</span>
							{/if}
						{/if}
					</div>
				</div>
				<button aria-label={$i18n.t('Close')} onclick={() => (show = false)}>
					<span class="i-lucide-x size-3.5 block"></span>
				</button>
			</div>

			{#if !isImage && !isPdf}
				<div class="text-sm text-content-muted mt-0.5">
					{$i18n.t(
						'Previewing the text extracted from the original data. Formatting and content may differ.'
					)}
				</div>
			{/if}

			{#if !isImage && extraction === 'success'}
				<div
					class="flex items-center gap-3 mt-3 text-sm border-b border-gray-100 dark:border-gray-850"
				>
					<button
						class="pb-1 -mb-px border-b-2 {selectedTab === 'preview'
							? 'border-gray-800 dark:border-gray-100 text-content font-medium'
							: 'border-transparent'}"
						onclick={() => (selectedTab = 'preview')}
					>
						{$i18n.t('Preview')}
					</button>
					<button
						class="pb-1 -mb-px border-b-2 {selectedTab === 'content'
							? 'border-gray-800 dark:border-gray-100 text-content font-medium'
							: 'border-transparent'}"
						onclick={() => (selectedTab = 'content')}
					>
						{$i18n.t('Content')}
					</button>
				</div>
			{/if}
		</div>

		<div class="max-h-[75vh] overflow-auto mt-2">
			{#if isImage}
				<img
					src={`${WEBUI_API_BASE_URL}/files/${fileId}/content`}
					alt={item?.name ?? 'Image'}
					class="max-h-[70vh] w-auto mx-auto rounded-lg"
				/>
			{:else if isPdf && (extraction !== 'success' || selectedTab === 'preview')}
				<iframe
					src={`${WEBUI_API_BASE_URL}/files/${fileId}/content`}
					title={item?.name ?? 'PDF'}
					class="w-full h-[70vh] rounded-lg border border-line!"
				></iframe>
			{:else if extraction === 'failed'}
				<div class="text-sm whitespace-pre-wrap text-red-500 dark:text-red-400">
					{content}
				</div>
			{:else if extraction === 'none'}
				<div class="text-sm text-content-subtle">
					{$i18n.t('This file has not been extracted to text yet.')}
				</div>
			{:else if selectedTab === 'content'}
				<pre
					class="text-xs whitespace-pre-wrap break-words bg-gray-50 dark:bg-gray-850 rounded-lg p-3 overflow-auto"><code
						>{content}</code
					></pre>
			{:else}
				<div class="markdown-prose text-sm">
					<Markdown id={`file-preview-${item?.id ?? ''}`} {content} />
				</div>
			{/if}
		</div>

		{#if download || onDelete || onShare}
			<div class="flex justify-end items-center gap-0.5 mt-3">
				{#if onShare}
					<Tooltip content={$i18n.t('Access Control')}>
						<button
							class="flex items-center gap-1.5 px-3 py-1.5 text-sm text-content dark:hover:text-white hover:bg-black/5 dark:hover:bg-white/5 rounded-xl transition"
							type="button"
							onclick={() => onShare?.()}
						>
							{#if visibility === 'public'}
								<span class="i-lucide-globe size-4 block"></span>
							{:else if visibility === 'shared'}
								<span class="i-lucide-users size-4 block"></span>
							{:else}
								<span class="i-lucide-lock size-4 block"></span>
							{/if}
							<span>{$i18n.t('Share')}</span>
						</button>
					</Tooltip>
				{/if}

				{#if download && fileId}
					<a
						class="flex items-center gap-1.5 px-3 py-1.5 text-sm text-content dark:hover:text-white hover:bg-black/5 dark:hover:bg-white/5 rounded-xl transition"
						href={`${WEBUI_API_BASE_URL}/files/${fileId}/content?attachment=true`}
					>
						<span class="i-lucide-download size-4 block"></span>
						<span>{$i18n.t('Download')}</span>
					</a>
				{/if}

				{#if onDelete}
					<button
						class="flex items-center gap-1.5 px-3 py-1.5 text-sm text-content dark:hover:text-white hover:bg-black/5 dark:hover:bg-white/5 rounded-xl transition"
						type="button"
						onclick={() => onDelete?.()}
					>
						<span class="i-lucide-trash-2 size-4 block"></span>
						<span>{$i18n.t('Delete')}</span>
					</button>
				{/if}
			</div>
		{/if}
	</div>
</Modal>
