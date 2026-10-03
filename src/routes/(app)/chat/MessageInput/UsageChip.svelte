<script lang="ts">
	// 入力欄右上の floating トークンゲージ(#102)— コンテキスト占有の常時表示。
	// 生成中は live usage(total_tokens が現在占有)、それ以外は直近ターンの確定値
	// (chat:completion のマージ済み usage / leaf message usage — 次ターンへ持ち越す
	// carried_tokens が占有、無ければ prompt_tokens。contextUsageFromUsage に揃える)。
	// どちらも無ければ何も描かない(新規チャット等)。
	// ターンごとの所要時間・tok/s はここではなく各メッセージのアクション行に出す。
	import { chatId, mobile } from '$lib/stores';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import UsageGauge from '$lib/components/common/UsageGauge.svelte';
	import {
		contextUsageFromUsage,
		formatTokens,
		getActiveLiveUsage,
		getChatUsageBaseline
	} from '$lib/stores/live-usage.svelte';

	// store は chatId スコープ(#107 W-A)— 表示中チャットの分だけを読む。
	// 別チャット(subagent の子セッション含む)の生成がここに混線しない。
	const live = $derived(getActiveLiveUsage($chatId));
	const baseline = $derived(getChatUsageBaseline($chatId));

	const current = $derived(
		live ? live.total_tokens : (contextUsageFromUsage(baseline)?.tokens ?? null)
	);
	const contextSize = $derived(live?.context_size ?? baseline?.context_size ?? null);
	const inTokens = $derived(baseline?.prompt_tokens);
	const outTokens = $derived(baseline?.completion_tokens);
	const hasInOut = $derived(inTokens !== undefined || outTokens !== undefined);

	// 狭い画面では donut + 数値だけにし、詳細(IN/OUT)は tooltip に畳む
	const details = $derived(
		hasInOut ? `IN ${formatTokens(inTokens ?? 0)} / OUT ${formatTokens(outTokens ?? 0)}` : ''
	);
</script>

{#if current !== null}
	<Tooltip
		content={$mobile ? details : ''}
		placement="top-end"
		className="flex pointer-events-auto"
	>
		<div
			class="flex items-center gap-1.5 bg-surface-raised border border-line rounded-full px-2.5 py-1 text-xs text-content-muted tabular-nums"
		>
			{#if contextSize}
				<UsageGauge {current} total={contextSize} class="size-3.5 shrink-0" />
				<span>{formatTokens(current)}/{formatTokens(contextSize)}</span>
			{:else}
				<span>{formatTokens(current)}</span>
			{/if}
			{#if !$mobile && hasInOut}
				<span class="text-content-subtle">·</span>
				<span>IN {formatTokens(inTokens ?? 0)} / OUT {formatTokens(outTokens ?? 0)}</span>
			{/if}
		</div>
	</Tooltip>
{/if}
