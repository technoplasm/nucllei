<script>
	import { replaceTokens, processResponseContent } from '$lib/utils';
	import { user } from '$lib/stores';

	// #22 L2: marked → markdown-it。setext(lheading)・参照リンク定義を文法ごと無効化。
	// トークンは marked 互換形に変換され、MarkdownTokens.svelte がそのまま消費する。
	import { lex } from '$lib/utils/markdown-it';

	import MarkdownTokens from './Markdown/MarkdownTokens.svelte';
	import { createEventDispatcher } from 'svelte';

	const dispatch = createEventDispatcher();

	export let id = '';
	export let content;
	export let model = null;
	export let save = false;

	export let sourceIds = [];

	export let onSourceClick = () => {};
	export let onTaskClick = () => {};

	let tokens = [];

	$: (async () => {
		if (content) {
			tokens = lex(
				replaceTokens(processResponseContent(content), sourceIds, model?.name, $user?.name)
			);
		}
	})();
</script>

{#key id}
	<MarkdownTokens
		{tokens}
		{id}
		{save}
		{onTaskClick}
		{onSourceClick}
		on:update={(e) => {
			dispatch('update', e.detail);
		}}
		on:code={(e) => {
			dispatch('code', e.detail);
		}}
	/>
{/key}
