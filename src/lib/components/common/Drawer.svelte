<script lang="ts">
	import { onDestroy, untrack, type Snippet } from 'svelte';
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

	// body のスクロール止めは、body 直下に portal 中の data-scroll-lock が 1 つも残って
	// いないときだけ解く (common/Modal.svelte と同じ。重ねたモーダルの下を守る)
	const releaseScrollLock = () => {
		if (!document.body.querySelector(':scope > [data-scroll-lock]')) {
			document.body.style.overflow = 'unset';
		}
	};

	// Switch.svelte と同じガード: 初期値では発火させず、show が実際に
	// true → false に変化したときだけ onClose を呼ぶ。
	let lastShow = show;
	$effect(() => {
		if (show !== lastShow) {
			if (!show) {
				untrack(() => onClose?.());
			}
			lastShow = show;
		}
	});

	// 後始末は cleanup で返す (common/Modal.svelte・ConfirmDialog と同じ形)。要素は
	// ローカルに取ってから使う。Drawer の要素は {#if} の外なので、閉じている間は
	// 元の位置からも外しておく (開くと body 直下へ portal する)
	$effect(() => {
		const el = modalElement;
		if (!el) return;
		if (!show) {
			el.remove();
			return;
		}
		document.body.appendChild(el);
		window.addEventListener('keydown', handleKeyDown);
		document.body.style.overflow = 'hidden';
		return () => {
			window.removeEventListener('keydown', handleKeyDown);
			if (el.parentNode === document.body) {
				document.body.removeChild(el);
			}
			releaseScrollLock();
		};
	});

	onDestroy(() => {
		show = false;
		window.removeEventListener('keydown', handleKeyDown);
		if (modalElement && modalElement.parentNode === document.body) {
			document.body.removeChild(modalElement);
		}
		releaseScrollLock();
	});
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->

<div
	bind:this={modalElement}
	data-scroll-lock
	class="modal fixed right-0 {$isApp
		? ' ml-[4.5rem] max-w-[calc(100%-4.5rem)]'
		: ''} left-0 bottom-0 bg-black/60 w-full h-screen max-h-[100dvh] flex justify-center z-999 overflow-hidden overscroll-contain"
	in:fly={{ y: 100, duration: 100 }}
	onmousedown={() => {
		show = false;
	}}
>
	<div
		class=" mt-auto w-full bg-gray-50 dark:bg-gray-900 text-content {className} max-h-[100dvh] overflow-y-auto"
		onmousedown={(e) => {
			e.stopPropagation();
		}}
	>
		{@render children?.()}
	</div>
</div>
