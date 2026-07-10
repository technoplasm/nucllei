<script lang="ts">
	import { goto } from '$app/navigation';
	import { user } from '$lib/stores';
	import { onMount } from 'svelte';

	onMount(() => {
		// llens(#66): 非 admin は skill editor(workspace.skills)だけが Skills タブに入れる。
		if ($user?.role !== 'admin') {
			if ($user?.permissions?.workspace?.skills) {
				goto('/workspace/skills');
			} else {
				goto('/');
			}
		} else {
			goto('/workspace/models');
		}
	});
</script>
