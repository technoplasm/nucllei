<script lang="ts">
	// tool のエラー種別(サーバの旗 error_kind — README_API §5.1)のラベル。見た目(アイコン・色)
	// は tool-render の TOOL_ERROR_LOOKS、ラベルの文言はここ — i18next-parser は .svelte の
	// 文字列リテラルの t() しか拾わないので、キーを .ts の表に置かない。
	// ToolCallBlock / ExecutionBlock / BackgroundTasksChip で共用(本文は解釈しない)。
	import { getContext } from 'svelte';

	import { TOOL_ERROR_LOOKS, type ToolErrorKind } from '$lib/utils/tool-render';

	const i18n = getContext('i18n');

	let {
		kind,
		class: className = ''
	}: {
		kind: ToolErrorKind;
		class?: string;
	} = $props();

	const label = $derived.by(() => {
		if (kind === 'call') return $i18n.t('Retry needed');
		if (kind === 'failure') return $i18n.t('Failed');
		if (kind === 'outcome') return $i18n.t('Not found');
		if (kind === 'violation') return $i18n.t('Protocol violation');
		return $i18n.t('Error');
	});
</script>

<span class="{TOOL_ERROR_LOOKS[kind].textClass} {className}">{label}</span>
