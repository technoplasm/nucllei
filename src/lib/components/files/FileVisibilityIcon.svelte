<script lang="ts">
	import LockClosed from '$lib/components/icons/LockClosed.svelte';
	import GlobeAlt from '$lib/components/icons/GlobeAlt.svelte';
	import UsersSolid from '$lib/components/icons/UsersSolid.svelte';

	// llens(#69): file の共有状態 3 値アイコン。file の ACL 意味論(他エンティティと逆):
	// null=非公開 / {read:{public:true}}=全体公開 / {read:{group_ids,user_ids}}=グループ共有
	export let accessControl = null;
	export let className = 'size-4';

	$: state = accessControl === null ? 'private' : accessControl?.read?.public ? 'public' : 'shared';
</script>

{#if state === 'public'}
	<GlobeAlt {className} />
{:else if state === 'shared'}
	<UsersSolid {className} />
{:else}
	<LockClosed {className} />
{/if}
