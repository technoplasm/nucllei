<script lang="ts">
	import { getContext } from 'svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	const i18n = getContext('i18n');
</script>

<!--
	応答生成中(内容未着)のプレースホルダ。旧スケルトン(animate-pulse のグレーバー)を廃止。
	reasoning の「[Spinner] Thinking...」ヘッダと *完全に同寸*(text-sm / font-medium / gap-2 /
	size-4 spinner / shimmer / mb-1.5)にして、Preparing → Thinking の差し替えでガタつかせない。
	寸法を変える場合は ContentBlocks.svelte の reasoning Collapsible と Collapsible.svelte の
	ヘッダ(shimmer 行)も併せて揃えること。
-->
<div class="w-full space-y-1 mb-1.5 text-sm">
	<div class="w-fit font-medium flex items-center gap-2 text-gray-500 shimmer">
		<Spinner className="size-4" />
		<span>{$i18n.t('Preparing...')}</span>
	</div>
</div>
