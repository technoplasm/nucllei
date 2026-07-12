<script lang="ts">
	import { goto } from '$app/navigation';
	import { user } from '$lib/stores';
	import { onMount } from 'svelte';

	onMount(() => {
		// llens: モデルアクセス(= admin。Models タブと同じゲート)があればモデル、
		// 無ければファイル(全ログインユーザーに開く)を初期表示する。
		if ($user?.role === 'admin') {
			goto('/workspace/models');
		} else {
			goto('/workspace/files');
		}
	});
</script>
