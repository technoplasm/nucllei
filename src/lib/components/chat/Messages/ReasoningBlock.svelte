<script lang="ts">
	// reasoning(CoT)ブロックの表示(content_blocks、#17)。
	// 旧・汎用 Collapsible の attributes 経由レンダリングから独立させた専用コンポーネント。
	// レイアウトは ToolCallBlock と同型: ヘッダはバッジ(開くと枠上辺 tab に合体)、
	// 本文は枠、下辺左に閉じる・右にすべて閉じる(開きっぱなしを一括で畳む用)。
	import { getContext } from 'svelte';
	import { slide } from 'svelte/transition';
	import { quintOut } from 'svelte/easing';

	import dayjs from '$lib/dayjs';
	import dayjsDuration from 'dayjs/plugin/duration';
	import relativeTime from 'dayjs/plugin/relativeTime';

	dayjs.extend(dayjsDuration);
	dayjs.extend(relativeTime);

	import { settings } from '$lib/stores';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Markdown from './Markdown.svelte';

	const i18n = getContext('i18n');

	// dayjs.humanize() のロケール追従(i18n の言語リストから読めた最初のものを使う)
	async function loadLocale(locales: readonly string[]) {
		for (const locale of locales) {
			try {
				dayjs.locale(locale);
				break;
			} catch (error) {
				console.error(`Could not load locale '${locale}':`, error);
			}
		}
	}
	$effect(() => {
		loadLocale($i18n.languages);
	});

	let {
		id = '',
		content = '',
		// メッセージ確定後(中断含む)は block.done が立っていなくてもスピナーを止める保険
		done = false,
		duration = null,
		// メッセージ内一括開閉(ContentBlocks が採番)。seq が進んだら open に追従する
		expandAll = null,
		onExpandAll = undefined
	}: {
		id?: string;
		content?: string;
		done?: boolean;
		duration?: number | null;
		expandAll?: { seq: number; open: boolean } | null;
		onExpandAll?: (open: boolean) => void;
	} = $props();

	let open = $state($settings?.expandDetails ?? false);
	let rootElement: HTMLElement | null = $state(null);
	// 開閉コントロール(バッジ/tab)hover 中は開閉単位のボーダー全部を強調する
	let toggleHover = $state(false);

	// 初期値キャプチャは意図(seq 比較の lastState ガード — 変化時のみ $effect で追従)
	// svelte-ignore state_referenced_locally
	let lastExpandSeq = expandAll?.seq ?? 0;
	$effect(() => {
		if (expandAll && expandAll.seq !== lastExpandSeq) {
			lastExpandSeq = expandAll.seq;
			open = expandAll.open;
		}
	});

	// llens(#17): 長い展開の末尾からも閉じられるようにする
	const collapseFromBottom = () => {
		open = false;
		requestAnimationFrame(() => {
			rootElement?.scrollIntoView({ block: 'nearest' });
		});
	};
</script>

<!-- 注意: ルートに space-y を置かない — ヘッダ tab は -mb-px で枠上辺に密着させる -->
<div {id} class="w-full text-sm -ml-2" bind:this={rootElement}>
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<!-- svelte-ignore a11y_click_events_have_key_events -->
	<div
		class="w-fit cursor-pointer text-content-muted hover:text-content transition ms-2 px-2 py-0.5 border {toggleHover
			? 'border-line-hover'
			: 'border-line'} bg-gray-100 dark:bg-gray-850 {open
			? 'relative z-[1] -mb-px rounded-t-md border-b-0'
			: 'rounded-md'}"
		onpointerenter={() => (toggleHover = true)}
		onpointerleave={() => (toggleHover = false)}
		onpointerup={() => {
			open = !open;
		}}
	>
		<div class="font-medium flex items-center gap-2 {done ? '' : 'shimmer'}">
			{#if !done}
				<div>
					<Spinner className="size-4" />
				</div>
			{:else}
				<div class="text-blue-500 dark:text-blue-400 shrink-0">
					<span class="i-lucide-circle-check size-4 block"></span>
				</div>
			{/if}

			<div>
				<!-- duration は 0 秒がありうる(数値 0 / 文字列 "0" とも thought 扱い) -->
				{#if done && duration != null}
					{#if duration < 60}
						{$i18n.t('Thought for {{DURATION}} seconds', { DURATION: duration })}
					{:else}
						{$i18n.t('Thought for {{DURATION}}', {
							DURATION: dayjs.duration(duration, 'seconds').humanize()
						})}
					{/if}
				{:else}
					{$i18n.t('Thinking...')}
				{/if}
			</div>

			<div class="flex self-center translate-y-[1px]">
				{#if open}
					<span class="i-lucide-chevron-up size-3.5 block"></span>
				{:else}
					<span class="i-lucide-chevron-down size-3.5 block"></span>
				{/if}
			</div>
		</div>
	</div>

	{#if open}
		<div transition:slide={{ duration: 300, easing: quintOut, axis: 'y' }}>
			<!--
				reasoning は本文と同じ Markdown コンポーネントで描画する。
				XSS 不変条件: 生 <think> 内容はモデル出力なのでサニタイズが必須。
				Markdown は MarkdownTokens/InlineTokens 内で DOMPurify.sanitize を通してから
				{@html} するため本文と同じ信頼モデルで安全。ここで直接 {@html} を書いてはならない。
			-->
			<div
				class="border {toggleHover
					? 'border-line-hover'
					: 'border-line'} rounded-lg transition px-3 py-2 text-content"
				dir="auto"
			>
				<Markdown id={`${id}-reasoning`} {content} />
			</div>

			<div class="flex justify-between mx-2">
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<!-- svelte-ignore a11y_click_events_have_key_events -->
				<div
					class="relative cursor-pointer -mt-px flex items-center gap-1 text-xs font-medium text-content-muted hover:text-content transition bg-gray-100 dark:bg-gray-850 border border-t-0 {toggleHover
						? 'border-line-hover'
						: 'border-line'} rounded-b-md px-1.5 py-0.5"
					onpointerenter={() => (toggleHover = true)}
					onpointerleave={() => (toggleHover = false)}
					onpointerup={(e) => {
						e.stopPropagation();
						collapseFromBottom();
					}}
				>
					<span class="i-lucide-chevron-up size-3 block"></span>
					{$i18n.t('Close')}
				</div>

				{#if onExpandAll}
					<!-- svelte-ignore a11y_no_static_element_interactions -->
					<!-- svelte-ignore a11y_click_events_have_key_events -->
					<div
						class="relative cursor-pointer -mt-px flex items-center gap-1 text-xs font-medium text-content-muted hover:text-content transition bg-gray-100 dark:bg-gray-850 border border-t-0 {toggleHover
							? 'border-line-hover'
							: 'border-line'} rounded-b-md px-1.5 py-0.5"
						onpointerenter={() => (toggleHover = true)}
						onpointerleave={() => (toggleHover = false)}
						onpointerup={(e) => {
							e.stopPropagation();
							onExpandAll?.(false);
						}}
					>
						<span class="i-lucide-chevrons-down-up size-3 block"></span>
						{$i18n.t('Collapse all')}
					</div>
				{/if}
			</div>
		</div>
	{/if}
</div>
