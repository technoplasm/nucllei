<script lang="ts">
	import { getContext } from 'svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { subscribeTick, tickNow } from '$lib/utils/tick.svelte';

	const i18n = getContext('i18n');

	// tool 実行後の追撃待ち(ContentBlocks)では文言だけ差し替えて同寸で使い回す。
	// 翻訳済み文字列を受ける(キーを受けて t(label) すると i18next-parser の
	// 静的抽出に乗らず、keepRemoved: false でキーが消されるため)。
	let { label = undefined }: { label?: string } = $props();

	// 待ち時間(#100)。ここは「何も起きていないように見える」区間そのものなので、
	// 動いている証拠として経過秒を出す。起点はマウント時刻でよい(この表示が
	// 出ている間だけが待ち時間)。
	// 起点は 1 度だけ決める(reactive にしない)
	const startedAt = Date.now();
	$effect(() => subscribeTick());
	const elapsedSeconds = $derived(Math.max(0, Math.round((tickNow() - startedAt) / 1000)));
</script>

<!--
	応答待ち(内容未着)のインジケータ。初回の「Preparing...」と、tool 実行後の追撃待ち
	(ContentBlocks から label 指定)で共用。旧 Skeleton(animate-pulse のグレーバー)を廃止・改名。
	reasoning の「[Spinner] Thinking...」ヘッダと *完全に同寸*(text-sm / font-medium / gap-2 /
	size-4 spinner / shimmer / mb-1.5)にして、Preparing → Thinking の差し替えでガタつかせない。
	寸法を変える場合は ReasoningBlock.svelte / ToolCallBlock.svelte のヘッダ(shimmer 行)も
	併せて揃えること。
-->
<div class="w-full space-y-1 mb-1.5 text-sm">
	<div class="w-fit font-medium flex items-center gap-2 text-content-muted shimmer">
		<Spinner className="size-4" />
		<span>{label ?? $i18n.t('Preparing...')}</span>
		{#if elapsedSeconds > 0}
			<span class="tabular-nums">
				{$i18n.t('{{DURATION}} seconds', { DURATION: elapsedSeconds })}
			</span>
		{/if}
	</div>
</div>
