<script lang="ts">
	import { getContext } from 'svelte';

	const i18n = getContext('i18n');

	import ShortcutsModal from './ShortcutsModal.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	let showShortcuts = $state(false);
</script>

<div class="hidden lg:flex fixed bottom-0 right-0 px-1 py-1 z-20">
	<!-- Ctrl+/(+layout のグローバルショートカット)のクリックターゲット -->
	<button
		id="show-shortcuts-button"
		class="hidden"
		aria-label={$i18n.t('Keyboard shortcuts')}
		onclick={() => {
			showShortcuts = !showShortcuts;
		}}
	></button>

	<Tooltip content={$i18n.t('Keyboard shortcuts')} placement="left">
		<button
			class="text-content-muted hover:text-content bg-surface-muted size-4 flex items-center justify-center text-[0.7rem] rounded-full"
			aria-label={$i18n.t('Keyboard shortcuts')}
			onclick={() => {
				showShortcuts = !showShortcuts;
			}}
		>
			?
		</button>
	</Tooltip>
</div>

<ShortcutsModal bind:show={showShortcuts} />
