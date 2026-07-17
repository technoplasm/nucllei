<script lang="ts">
	import type { Snippet } from 'svelte';

	let { children }: { children?: Snippet } = $props();
</script>

<div class=" self-center font-semibold line-clamp-1 flex gap-1 items-center">
	{@render children?.()}
</div>
