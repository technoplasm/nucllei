<script lang="ts">
	// llens(#19): クライアント側で検出した「存在しない」の in-place 表示。
	// +error.svelte(サーバー側 404)と同じ顔にして、URL を保ったまま 404 に見せる。
	let { message = '' }: { message?: string } = $props();
</script>

<div class="flex w-full h-full">
	<div class="m-auto my-10 text-content text-3xl font-semibold">
		404{message ? `: ${message}` : ''}
	</div>
</div>
