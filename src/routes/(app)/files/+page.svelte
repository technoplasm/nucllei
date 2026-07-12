<script lang="ts">
	// llens: ファイル管理は /workspace/files へ移設。/files は旧導線の後方互換リダイレクト。
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';

	onMount(() => {
		goto('/workspace/files');
	});
</script>
