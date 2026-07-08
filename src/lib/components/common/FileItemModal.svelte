<script lang="ts">
	import { getContext } from 'svelte';
	import { formatFileSize } from '$lib/utils';

	const i18n = getContext('i18n');

	import Modal from './Modal.svelte';
	import XMark from '../icons/XMark.svelte';
	import Markdown from '../chat/Messages/Markdown.svelte';

	export let item;
	export let show = false;
	export let edit = false;

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
</script>

<Modal bind:show size="lg">
	<div class="font-primary px-6 py-5 w-full flex flex-col justify-center dark:text-gray-400">
		<div class="pb-2">
			<div class="flex items-start justify-between">
				<div class="font-medium text-lg dark:text-gray-100 line-clamp-1">
					{item?.name ?? 'File'}
				</div>
				<button on:click={() => (show = false)}>
					<XMark />
				</button>
			</div>

			<div class="flex flex-wrap items-center text-sm gap-1.5 text-gray-500 mt-0.5">
				{#if item?.size}
					<span class="shrink-0">{formatFileSize(item.size)}</span>
					<span>•</span>
				{/if}
				<span>
					{$i18n.t(
						'Previewing the text extracted from the original data. Formatting and content may differ.'
					)}
				</span>

				{#if state === 'failed'}
					<span class="shrink-0 text-red-500 font-medium">・{$i18n.t('Extraction failed')}</span>
				{:else if state === 'none'}
					<span class="shrink-0 text-gray-400 font-medium">・{$i18n.t('Not extracted')}</span>
				{/if}
			</div>

			{#if state === 'success'}
				<div
					class="flex items-center gap-3 mt-3 text-sm border-b border-gray-100 dark:border-gray-850"
				>
					<button
						class="pb-1 -mb-px border-b-2 {selectedTab === 'content'
							? 'border-gray-800 dark:border-gray-100 text-gray-800 dark:text-gray-100 font-medium'
							: 'border-transparent'}"
						on:click={() => (selectedTab = 'content')}
					>
						{$i18n.t('Content')}
					</button>
					<button
						class="pb-1 -mb-px border-b-2 {selectedTab === 'preview'
							? 'border-gray-800 dark:border-gray-100 text-gray-800 dark:text-gray-100 font-medium'
							: 'border-transparent'}"
						on:click={() => (selectedTab = 'preview')}
					>
						{$i18n.t('Preview')}
					</button>
				</div>
			{/if}
		</div>

		<div class="max-h-[75vh] overflow-auto mt-2">
			{#if state === 'failed'}
				<div class="text-sm whitespace-pre-wrap text-red-500 dark:text-red-400">
					{content}
				</div>
			{:else if state === 'none'}
				<div class="text-sm text-gray-400 dark:text-gray-500">
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
	</div>
</Modal>
