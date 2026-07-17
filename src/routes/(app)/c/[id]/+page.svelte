<script lang="ts">
	import { page } from '$app/stores';

	import Chat from '../../chat/Chat.svelte';
	import Help from '../../chat/Help.svelte';
</script>

<Help />
<Chat chatIdProp={$page.params.id} />
