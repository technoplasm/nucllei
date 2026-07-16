<script lang="ts">
	import { onDestroy, type Snippet } from 'svelte';
	import { fly } from 'svelte/transition';
	import { isApp } from '$lib/stores';

	let {
		show = $bindable(false),
		className = '',
		onClose = undefined,
		children
	}: {
		show?: boolean;
		className?: string;
		onClose?: () => void;
		children?: Snippet;
	} = $props();

	let modalElement: HTMLDivElement | null = $state(null);

	const handleKeyDown = (event: KeyboardEvent) => {
		if (event.key === 'Escape' && isTopModal()) {
			show = false;
		}
	};

	const isTopModal = () => {
		const modals = document.getElementsByClassName('modal');
		return modals.length && modals[modals.length - 1] === modalElement;
	};

	// Switch.svelte と同じガード: 初期値では発火させず、show が実際に
	// true → false に変化したときだけ onClose を呼ぶ。
	let lastShow = show;
	$effect(() => {
		if (show && modalElement) {
			document.body.appendChild(modalElement);
			window.addEventListener('keydown', handleKeyDown);
			document.body.style.overflow = 'hidden';
		} else if (modalElement) {
			if (lastShow) {
				onClose?.();
			}
			window.removeEventListener('keydown', handleKeyDown);

			if (document.body.contains(modalElement)) {
				document.body.removeChild(modalElement);
				document.body.style.overflow = 'unset';
			}
		}
		lastShow = show;
	});

	onDestroy(() => {
		show = false;
		window.removeEventListener('keydown', handleKeyDown);
		if (modalElement) {
			if (document.body.contains(modalElement)) {
				document.body.removeChild(modalElement);
				document.body.style.overflow = 'unset';
			}
		}
	});
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->

<div
	bind:this={modalElement}
	class="modal fixed right-0 {$isApp
		? ' ml-[4.5rem] max-w-[calc(100%-4.5rem)]'
		: ''} left-0 bottom-0 bg-black/60 w-full h-screen max-h-[100dvh] flex justify-center z-999 overflow-hidden overscroll-contain"
	in:fly={{ y: 100, duration: 100 }}
	onmousedown={() => {
		show = false;
	}}
>
	<div
		class=" mt-auto w-full bg-gray-50 dark:bg-gray-900 text-content {className} max-h-[100dvh] overflow-y-auto scrollbar-hidden"
		onmousedown={(e) => {
			e.stopPropagation();
		}}
	>
		{@render children?.()}
	</div>
</div>
