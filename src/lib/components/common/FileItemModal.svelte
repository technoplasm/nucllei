<script lang="ts">
	import { getContext } from 'svelte';
	import { formatFileSize } from '$lib/utils';
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	const i18n = getContext('i18n');

	import Modal from './Modal.svelte';
	import XMark from '../icons/XMark.svelte';
	import Markdown from '../chat/Messages/Markdown.svelte';
	import Tooltip from './Tooltip.svelte';
	import ArrowDownTray from '../icons/ArrowDownTray.svelte';
	import GarbageBin from '../icons/GarbageBin.svelte';
	import LockClosed from '../icons/LockClosed.svelte';
	import GlobeAlt from '../icons/GlobeAlt.svelte';
	import UsersSolid from '../icons/UsersSolid.svelte';

	export let item;
	export let show = false;
	export let edit = false;

	// llens(#69): /files 管理ページ用の拡張(すべて省略可 — チャット側の既存利用は不変)。
	// download=true でダウンロードリンク、onDelete/onShare はフッターのボタンから callback。
	export let download = false;
	export let onDelete: null | Function = null;
	export let onShare: null | Function = null;

	let selectedTab: 'content' | 'preview' = 'content';

	// llens(#52): 添付ファイルは「モデルにどう見えるか(= 抽出された content)」を見せる。
	// content は md 前提。コンテンツ=md のソース(code block)、プレビュー=md をレンダリング。
	// 状態は attachment が制御(success/failed)。未抽出(none)= status も content も無い。
	$: data = item?.file?.data ?? {};
	$: content = data?.content ?? '';
	$: status = data?.status; // 'success' | 'failed' | undefined
	$: state =
		status === 'failed'
			? 'failed'
			: content.trim() === '' && status !== 'success'
				? 'none'
				: 'success';

	// llens(#69): 画像 / PDF は元データをそのままプレビュー(content 取得は同一 URL)。
	$: fileId = item?.file?.id ?? item?.id;
	$: contentType = item?.file?.meta?.content_type ?? item?.meta?.content_type ?? '';
	$: isImage = contentType.startsWith('image/');
	$: isPdf = contentType === 'application/pdf';

	// file の ACL 意味論: null=非公開 / read.public=全体公開 / それ以外=グループ共有
	$: accessControl = item?.file?.access_control ?? null;
	$: visibility =
		accessControl === null ? 'private' : accessControl?.read?.public ? 'public' : 'shared';
</script>

<Modal bind:show size="lg">
	<div class="font-primary px-6 py-5 w-full flex flex-col justify-center text-content-muted">
		<div class="pb-2">
			<div class="flex items-start justify-between">
				<div class="font-medium text-lg text-content line-clamp-1">
					{item?.name ?? 'File'}
				</div>
				<button on:click={() => (show = false)}>
					<XMark />
				</button>
			</div>

			<div class="flex flex-wrap items-center text-sm gap-1.5 text-content-muted mt-0.5">
				{#if item?.size}
					<span class="shrink-0">{formatFileSize(item.size)}</span>
					<span>•</span>
				{/if}
				{#if isImage || isPdf}
					<span class="shrink-0">{contentType}</span>
				{:else}
					<span>
						{$i18n.t(
							'Previewing the text extracted from the original data. Formatting and content may differ.'
						)}
					</span>

					{#if state === 'failed'}
						<span class="shrink-0 text-red-500 font-medium">・{$i18n.t('Extraction failed')}</span>
					{:else if state === 'none'}
						<span class="shrink-0 text-content-subtle font-medium">・{$i18n.t('Not extracted')}</span>
					{/if}
				{/if}
			</div>

			{#if !isImage && !isPdf && state === 'success'}
				<div
					class="flex items-center gap-3 mt-3 text-sm border-b border-gray-100 dark:border-gray-850"
				>
					<button
						class="pb-1 -mb-px border-b-2 {selectedTab === 'content'
							? 'border-gray-800 dark:border-gray-100 text-content font-medium'
							: 'border-transparent'}"
						on:click={() => (selectedTab = 'content')}
					>
						{$i18n.t('Content')}
					</button>
					<button
						class="pb-1 -mb-px border-b-2 {selectedTab === 'preview'
							? 'border-gray-800 dark:border-gray-100 text-content font-medium'
							: 'border-transparent'}"
						on:click={() => (selectedTab = 'preview')}
					>
						{$i18n.t('Preview')}
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
			{:else if isPdf}
				<iframe
					src={`${WEBUI_API_BASE_URL}/files/${fileId}/content`}
					title={item?.name ?? 'PDF'}
					class="w-full h-[70vh] rounded-lg border border-line!"
				></iframe>
			{:else if state === 'failed'}
				<div class="text-sm whitespace-pre-wrap text-red-500 dark:text-red-400">
					{content}
				</div>
			{:else if state === 'none'}
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
							on:click={() => onShare()}
						>
							{#if visibility === 'public'}
								<GlobeAlt className="size-4" />
							{:else if visibility === 'shared'}
								<UsersSolid className="size-4" />
							{:else}
								<LockClosed className="size-4" />
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
						<ArrowDownTray className="size-4" />
						<span>{$i18n.t('Download')}</span>
					</a>
				{/if}

				{#if onDelete}
					<button
						class="flex items-center gap-1.5 px-3 py-1.5 text-sm text-content dark:hover:text-white hover:bg-black/5 dark:hover:bg-white/5 rounded-xl transition"
						type="button"
						on:click={() => onDelete()}
					>
						<GarbageBin className="size-4" />
						<span>{$i18n.t('Delete')}</span>
					</button>
				{/if}
			</div>
		{/if}
	</div>
</Modal>
