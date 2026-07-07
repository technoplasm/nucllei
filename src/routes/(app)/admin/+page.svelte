<script lang="ts">
	// llens(#48): Admin を Workspace に統合。/admin は /workspace へリダイレクト(旧導線の後方互換)。
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';

	onMount(() => {
		goto('/workspace');
	});
</script>
