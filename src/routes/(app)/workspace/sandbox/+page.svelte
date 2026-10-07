<script lang="ts">
	// #150: sandbox のファイルブラウザ(本体は components/workspace/Sandbox)。
	// SANDBOX_URL の無い配備ではタブを出さず、直接開かれたら layout が / へ戻す。
	import SandboxFileBrowser from '$lib/components/workspace/Sandbox/SandboxFileBrowser.svelte';
</script>

<SandboxFileBrowser />
