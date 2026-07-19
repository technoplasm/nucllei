<script lang="ts">
	import katex from 'katex';
	import 'katex/contrib/mhchem';
	import 'katex/dist/katex.min.css';

	let {
		content,
		displayMode = false
	}: {
		content: string;
		displayMode?: boolean;
	} = $props();
</script>

<!-- eslint-disable-next-line svelte/no-at-html-tags -- #17 XSS sink 監査済み(サニタイズは各経路) -->
{@html katex.renderToString(content, { displayMode, throwOnError: false })}
