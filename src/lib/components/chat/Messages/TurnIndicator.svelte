<script lang="ts">
	import { getContext } from 'svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import UsageGauge from '$lib/components/common/UsageGauge.svelte';
	import { formatTokens, getLiveUsage } from '$lib/stores/live-usage.svelte';
	import { subscribeTick, tickNow } from '$lib/utils/tick.svelte';

	const i18n = getContext('i18n');

	// label: undefined = 既定の「Preparing...」/ null = ラベル無し(スピナー+メトリクスのみ)。
	// 翻訳済み文字列を受ける(キーを受けて t(label) すると i18next-parser の
	// 静的抽出に乗らず、keepRemoved: false でキーが消されるため)。
	// messageId を渡すと live usage(chat:usage、#102)のメトリクスを右側に出す。
	let { label = undefined, messageId = undefined }: { label?: string | null; messageId?: string } =
		$props();

	// 待ち時間(#100)。「何も起きていないように見える」区間で動いている証拠として
	// 経過秒を出す。起点はマウント時刻と usage 初着(live.startedAt)の早い方 —
	// 再マウント(裸表示 → ContentBlocks 常駐への切替)でも時計が巻き戻らない。
	const mountedAt = Date.now();
	$effect(() => subscribeTick());

	const live = $derived(messageId ? getLiveUsage(messageId) : null);
	const startedAt = $derived(Math.min(mountedAt, live?.startedAt ?? Number.POSITIVE_INFINITY));
	const elapsedSeconds = $derived(Math.max(0, Math.round((tickNow() - startedAt) / 1000)));
</script>

<!--
	ターン進行中インジケータ(旧 PendingIndicator を常駐化・改名 — #102)。応答待ち(内容未着)の
	「Preparing...」、tool 実行後の追撃待ち(ContentBlocks から label 指定)、ターン進行中の常駐
	(label: null)で共用。live usage があれば donut ゲージ + current/total + tok/s を右側に出す。
	左端の Spinner+ラベル部は reasoning の「[Spinner] Thinking...」ヘッダと *完全に同寸*
	(text-sm / font-medium / gap-2 / size-4 spinner / shimmer / mb-1.5)にして、
	Preparing → Thinking の差し替えでガタつかせない。寸法を変える場合は
	ReasoningBlock.svelte / ToolCallBlock.svelte のヘッダ(shimmer 行)も併せて揃えること。
-->
<div class="w-full space-y-1 mb-1.5 text-sm">
	<div class="flex items-center gap-3">
		<div class="w-fit font-medium flex items-center gap-2 text-content-muted shimmer">
			<Spinner className="size-4" />
			{#if label !== null}
				<span>{label ?? $i18n.t('Preparing...')}</span>
			{/if}
			{#if elapsedSeconds > 0}
				<span class="tabular-nums">
					{$i18n.t('{{DURATION}} seconds', { DURATION: elapsedSeconds })}
				</span>
			{/if}
		</div>
		{#if live}
			<!-- 数値は通常のインク色(severity はゲージの fill だけが運ぶ)-->
			<div class="flex items-center gap-1.5 text-xs text-content-muted tabular-nums">
				{#if live.context_size}
					<UsageGauge current={live.total_tokens} total={live.context_size} class="size-3.5" />
					<span>{formatTokens(live.total_tokens)}/{formatTokens(live.context_size)}</span>
				{:else}
					<span>{formatTokens(live.total_tokens)}</span>
				{/if}
				{#if live.rate !== null && live.rate > 0}
					<span>{Math.round(live.rate)} tok/s</span>
				{/if}
			</div>
		{/if}
	</div>
</div>
