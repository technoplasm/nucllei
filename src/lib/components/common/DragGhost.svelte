<script lang="ts">
	import { onDestroy, onMount, type Snippet } from 'svelte';

	let { x, y, children }: { x: number; y: number; children?: Snippet } = $props();

	let popupElement: HTMLDivElement | null = null;

	onMount(() => {
		if (popupElement) {
			document.body.appendChild(popupElement);
		}
		document.body.style.overflow = 'hidden';
	});

	onDestroy(() => {
		// {#if} teardown と競合し得るため body 直下にいるときだけ外す(portal の NotFoundError 対策)
		if (popupElement && popupElement.parentNode === document.body) {
			document.body.removeChild(popupElement);
		}
		document.body.style.overflow = 'unset';
	});
</script>

<div
	bind:this={popupElement}
	class="fixed top-0 left-0 w-screen h-[100dvh] z-50 touch-none pointer-events-none"
>
	<div class=" absolute text-white z-99999" style="top: {y + 10}px; left: {x + 10}px;">
		{@render children?.()}
	</div>
</div>
