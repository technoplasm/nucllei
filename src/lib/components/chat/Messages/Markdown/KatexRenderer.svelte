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

{@html katex.renderToString(content, { displayMode, throwOnError: false })}
