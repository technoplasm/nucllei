<script lang="ts">
	import { getContext } from 'svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { chatId } from '$lib/stores';
	import { getLiveUsage } from '$lib/stores/live-usage.svelte';

	const i18n = getContext('i18n');

	// label: undefined = 既定の「Preparing...」/ null = ラベル無し。
	// 翻訳済み文字列を受ける(キーを受けて t(label) すると i18next-parser の
	// 静的抽出に乗らず、keepRemoved: false でキーが消されるため)。
	// messageId を渡すと live usage(chat:usage、#102)の tok/s を出す。
	//
	// スピナーは label があるときだけ(2026-08-09 決定): reasoning / tool のブロックが
	// 自前のぐるぐるを持つので、テキスト生成中の常駐スピナーは二重になってうるさい。
	// 「画面に動く要素が無い区間」(Preparing / 追撃待ち)だけが従来どおりスピナーを出し、
	// 生成中の常駐は ⚡ tok/s の 1 行だけにする。どちらも無ければ何も描かない。
	let { label = undefined, messageId = undefined }: { label?: string | null; messageId?: string } =
		$props();

	// live usage は chatId スコープ(#107 W-A)。表示中チャット($chatId)の分を読む —
	// 子セッションの readOnly transcript(別 chatId)では entry が無く rate は出ない(仕様)。
	const live = $derived(messageId ? getLiveUsage($chatId, messageId) : null);
	const rate = $derived(live !== null && live.rate !== null && live.rate > 0 ? live.rate : null);
</script>

{#if label !== null || rate !== null}
	<!--
		ターン進行中インジケータ(旧 PendingIndicator — #102)。応答待ち(内容未着)の
		「Preparing...」、tool 実行後の追撃待ち(ContentBlocks から label 指定)、生成中の
		⚡ tok/s で共用。左端の Spinner+ラベル部は reasoning の「[Spinner] Thinking...」ヘッダと
		*完全に同寸*(text-sm / font-medium / gap-2 / size-4 spinner / shimmer / mb-1.5)にして、
		Preparing → Thinking の差し替えでガタつかせない。寸法を変える場合は
		ReasoningBlock.svelte / ToolCallBlock.svelte のヘッダ(shimmer 行)も併せて揃えること。
	-->
	<div class="w-full space-y-1 mb-1.5 text-sm">
		<div class="flex items-center gap-3">
			{#if label !== null}
				<div class="w-fit font-medium flex items-center gap-2 text-content-muted shimmer">
					<Spinner className="size-4" />
					<span>{label ?? $i18n.t('Preparing...')}</span>
				</div>
			{/if}
			{#if rate !== null}
				<!-- 数値は通常のインク色(severity は UsageChip のゲージ側だけが運ぶ)-->
				<span class="flex items-center gap-1 text-xs text-content-muted tabular-nums">
					<span class="i-lucide-zap size-3 block"></span>
					{Math.round(rate)} tok/s
				</span>
			{/if}
		</div>
	</div>
{/if}
