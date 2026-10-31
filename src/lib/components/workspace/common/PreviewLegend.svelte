<script lang="ts">
	// プレビューの凡例と、トークン数の概算の式 (ExpandedText の色と同じ)
	import { getContext } from 'svelte';

	const i18n = getContext('i18n');
</script>

<div class="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-content-muted">
	<span class="inline-flex items-center gap-1">
		<span class="inline-block size-3 bg-amber-200/70 dark:bg-amber-400/25 rounded-xs"></span>
		{$i18n.t('Differs per user')}
	</span>
	<span class="inline-flex items-center gap-1">
		<span class="inline-block size-3 bg-sky-200/70 dark:bg-sky-400/25 rounded-xs"></span>
		{$i18n.t('Changes with the date and time')}
	</span>
	<span class="inline-flex items-center gap-1">
		<span class="underline decoration-dotted underline-offset-2">abc</span>
		{$i18n.t('Set by the model')}
	</span>
	<span class="inline-flex items-center gap-1">
		<span
			class="px-0.5 rounded-xs line-through text-red-700 bg-red-100 dark:text-red-300 dark:bg-red-500/20"
			>{'{{…}}'}</span
		>
		{$i18n.t('Not expanded here (empty)')}
	</span>
	<span>{$i18n.t('Everything else is the same for every user of this model.')}</span>
	<span>
		{$i18n.t(
			'Token counts are estimates (4 ASCII characters ≈ 1 token, other characters ≈ 1 each).'
		)}
	</span>
</div>
