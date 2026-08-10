<script lang="ts">
	// reasoning(CoT)ブロックの表示(content_blocks、#17)。
	// 旧・汎用 Collapsible の attributes 経由レンダリングから独立させた専用コンポーネント。
	// レイアウトは ToolCallBlock と同型: ヘッダはバッジ(開くと枠上辺 tab に合体)、
	// 本文は枠、下辺左に閉じる・右にすべて閉じる(チャット内で開きっぱなしを一括で畳む用)。
	import { getContext } from 'svelte';
	import { slide } from 'svelte/transition';
	import { quintOut } from 'svelte/easing';

	import dayjs from '$lib/dayjs';
	import dayjsDuration from 'dayjs/plugin/duration';
	import relativeTime from 'dayjs/plugin/relativeTime';

	dayjs.extend(dayjsDuration);
	dayjs.extend(relativeTime);

	import { settings } from '$lib/stores';
	import { formatDurationSeconds } from '$lib/utils/duration';
	import { subscribeTick, tickNow } from '$lib/utils/tick.svelte';
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
		// チャット内一括開閉(collapse-all.svelte.ts が採番)。seq が進んだら open に追従する
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

	// 思考中の経過時間(#100)。reasoning ブロックは最初の reasoning デルタで生えるので、
	// マウント時刻がそのまま起点になる(tool と違い同時に複数は走らない)。
	// 起点は 1 度だけ決める(reactive にしない)
	const startedAt = Date.now();
	$effect(() => {
		if (done) return;
		return subscribeTick();
	});
	const elapsedLabel = $derived.by(() => {
		if (done) return '';
		const seconds = Math.max(0, Math.round((tickNow() - startedAt) / 1000));
		return $i18n.t('Thinking for {{DURATION}} seconds', { DURATION: seconds });
	});
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

	// llens(#17): 長い展開を閉じたあとの視点合わせ。slide の outro 完了
	// (= ジオメトリ確定)後に scrollIntoView する — rAF 1 回では 300ms の収縮中に
	// 測ってしまい、ビューポートより背の高い展開では 'nearest' が no-op になって、
	// 収縮後の視界が無関係な位置に取り残される。
	// フラグ方式なのは「すべて閉じる」の巻き添えで閉じる他ブロックまで
	// スクロールを取り合わないため(合図を出した 1 ブロックだけが復帰する)。
	let scrollOnClose = false;
	const collapseFromBottom = () => {
		scrollOnClose = true;
		open = false;
	};
	const restoreViewAfterCollapse = () => {
		if (!scrollOnClose) return;
		scrollOnClose = false;
		rootElement?.scrollIntoView({ block: 'nearest' });
	};
</script>

<!-- 注意: ルートに space-y を置かない — ヘッダ tab は -mb-px で枠上辺に密着させる -->
<div {id} class="w-full text-sm -ml-2" bind:this={rootElement}>
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="w-fit cursor-pointer text-content-muted hover:text-content transition ms-2 px-2 py-0.5 border {toggleHover
			? 'border-line-hover'
			: 'border-line'} bg-gray-100 dark:bg-gray-850 {open
			? 'relative z-[1] -mb-px rounded-t-md border-b-0'
			: 'rounded-md'}"
		onpointerenter={() => (toggleHover = true)}
		onpointerleave={() => (toggleHover = false)}
		onpointerup={() => {
			if (open) scrollOnClose = true;
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
				<!-- duration は 0 秒がありうる(数値 0 / 文字列 "0" とも thought 扱い)。
				     #100 で小数保持になったので、丸めは formatDurationSeconds に一任する
				     (生値だと「12.347000122秒間の思考」になる)。 -->
				{#if done && duration != null}
					{#if duration < 60}
						{$i18n.t('Thought for {{DURATION}} seconds', {
							DURATION: formatDurationSeconds(duration)
						})}
					{:else}
						{$i18n.t('Thought for {{DURATION}}', {
							DURATION: dayjs.duration(duration, 'seconds').humanize()
						})}
					{/if}
				{:else}
					<span class="tabular-nums">{elapsedLabel}</span>
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
		<div
			transition:slide={{ duration: 300, easing: quintOut, axis: 'y' }}
			onoutroend={restoreViewAfterCollapse}
		>
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
					<div
						class="relative cursor-pointer -mt-px flex items-center gap-1 text-xs font-medium text-content-muted hover:text-content transition bg-gray-100 dark:bg-gray-850 border border-t-0 {toggleHover
							? 'border-line-hover'
							: 'border-line'} rounded-b-md px-1.5 py-0.5"
						onpointerenter={() => (toggleHover = true)}
						onpointerleave={() => (toggleHover = false)}
						onpointerup={(e) => {
							e.stopPropagation();
							// 一括で畳んだあとも、押したこのブロックは視界に戻す
							scrollOnClose = true;
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
