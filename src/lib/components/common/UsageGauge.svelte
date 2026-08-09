<script lang="ts">
	// トークン使用率の donut メーター(#102)。ratio-against-limit の表現で、
	// fill の色だけが severity を運ぶ(数値テキストは呼び手側で通常のインク色に置く)。
	// ※ CLAUDE.md の「インライン SVG 禁止」はアイコン規約 — データ表現の SVG は対象外。
	let {
		current,
		total,
		class: className = 'size-4'
	}: {
		current: number;
		total: number;
		/** サイズは class で渡す(size-4 相当を既定)。 */
		class?: string;
	} = $props();

	// viewBox 24 に対し stroke 3(サイズの 1/8)— 線は細く保つ
	const STROKE = 3;
	const R = (24 - STROKE) / 2;
	const C = 2 * Math.PI * R;

	const ratio = $derived(total > 0 ? Math.min(1, Math.max(0, current / total)) : 0);
	// 通常はニュートラル、≥70% で warning(amber)、≥85% で danger(red)
	const fillClass = $derived(
		ratio >= 0.85
			? 'stroke-red-500 dark:stroke-red-400'
			: ratio >= 0.7
				? 'stroke-amber-500 dark:stroke-amber-400'
				: 'stroke-gray-500 dark:stroke-gray-400'
	);
</script>

{#if total > 0}
	<!-- 数値は呼び手側がリング横に出す(リング内は小さすぎる)— ここは装飾扱い -->
	<svg viewBox="0 0 24 24" class={className} fill="none" aria-hidden="true">
		<circle
			cx="12"
			cy="12"
			r={R}
			stroke-width={STROKE}
			class="stroke-gray-200 dark:stroke-gray-700"
		/>
		<circle
			cx="12"
			cy="12"
			r={R}
			stroke-width={STROKE}
			stroke-linecap="round"
			stroke-dasharray="{ratio * C} {C}"
			transform="rotate(-90 12 12)"
			class={fillClass}
		/>
	</svg>
{/if}
