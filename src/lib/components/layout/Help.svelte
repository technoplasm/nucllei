<script lang="ts">
	import { onMount, tick, getContext } from 'svelte';

	const i18n = getContext('i18n');

	import ShortcutsModal from '../chat/ShortcutsModal.svelte';
	import Tooltip from '../common/Tooltip.svelte';

	let showShortcuts = false;
</script>

<div class=" hidden lg:flex fixed bottom-0 right-0 px-1 py-1 z-20">
	<button
		id="show-shortcuts-button"
		class="hidden"
		aria-label={$i18n.t('Keyboard shortcuts')}
		on:click={() => {
			showShortcuts = !showShortcuts;
		}}
	></button>

	<Tooltip content={$i18n.t('Keyboard shortcuts')} placement="left">
		<button
			class="text-content bg-gray-300/20 size-4 flex items-center justify-center text-[0.7rem] rounded-full"
			aria-label={$i18n.t('Keyboard shortcuts')}
			on:click={() => {
				showShortcuts = !showShortcuts;
			}}
		>
			?
		</button>
	</Tooltip>
</div>

<ShortcutsModal bind:show={showShortcuts} />
