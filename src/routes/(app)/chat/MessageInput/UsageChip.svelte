<script lang="ts">
	// 入力欄右上の floating トークンゲージ(#102)。
	// 生成中は live usage(total_tokens が現在占有)、それ以外は直近ターンの確定値
	// (chat:completion の usage / Chat.meta.usage — prompt_tokens が占有)。
	// どちらも無ければ何も描かない(新規チャット等)。
	import { mobile } from '$lib/stores';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import UsageGauge from '$lib/components/common/UsageGauge.svelte';
	import {
		formatTokens,
		getActiveLiveUsage,
		getChatUsageBaseline,
		getLastTurnRate
	} from '$lib/stores/live-usage.svelte';

	const live = $derived(getActiveLiveUsage());
	const baseline = $derived(getChatUsageBaseline());
	// 平均 tok/s は直近ターンの実測(lastTurnRate)— 生成中も確定値の方を出す
	const rate = $derived(getLastTurnRate());

	const current = $derived(
		live ? live.total_tokens : (baseline?.prompt_tokens ?? baseline?.total_tokens ?? null)
	);
	const contextSize = $derived(live?.context_size ?? baseline?.context_size ?? null);
	const inTokens = $derived(baseline?.prompt_tokens);
	const outTokens = $derived(baseline?.completion_tokens);
	const hasInOut = $derived(inTokens !== undefined || outTokens !== undefined);

	// 狭い画面では donut + 数値だけにし、詳細(tok/s・IN/OUT)は tooltip に畳む
	const details = $derived.by(() => {
		const parts: string[] = [];
		if (rate !== null && rate > 0) {
			parts.push(`${Math.round(rate)} tok/s`);
		}
		if (hasInOut) {
			parts.push(`IN ${formatTokens(inTokens ?? 0)} / OUT ${formatTokens(outTokens ?? 0)}`);
		}
		return parts.join(' · ');
	});
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
			{#if !$mobile}
				{#if rate !== null && rate > 0}
					<span class="text-content-subtle">·</span>
					<span>{Math.round(rate)} tok/s</span>
				{/if}
				{#if hasInOut}
					<span class="text-content-subtle">·</span>
					<span>IN {formatTokens(inTokens ?? 0)} / OUT {formatTokens(outTokens ?? 0)}</span>
				{/if}
			{/if}
		</div>
	</Tooltip>
{/if}
