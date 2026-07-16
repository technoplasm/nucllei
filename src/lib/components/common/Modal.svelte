<script lang="ts">
	import { onDestroy, type Snippet } from 'svelte';
	import { fade } from 'svelte/transition';

	import { flyAndScale } from '$lib/utils/transitions';

	let {
		show = $bindable(true),
		size = 'md',
		containerClassName = 'p-3',
		className = 'bg-white dark:bg-gray-900 rounded-2xl',
		children
	}: {
		show?: boolean;
		size?: 'xs' | 'sm' | 'md' | 'lg' | 'full';
		containerClassName?: string;
		className?: string;
		children?: Snippet;
	} = $props();

	let modalElement: HTMLDivElement | null = $state(null);

	const sizeToWidth = (size: string) => {
		if (size === 'full') {
			return 'w-full';
		}
		if (size === 'xs') {
			return 'w-[16rem]';
		} else if (size === 'sm') {
			return 'w-[30rem]';
		} else if (size === 'md') {
			return 'w-[42rem]';
		} else {
			return 'w-[56rem]';
		}
	};

	const handleKeyDown = (event: KeyboardEvent) => {
		if (event.key === 'Escape' && isTopModal()) {
			show = false;
		}
	};

	const isTopModal = () => {
		const modals = document.getElementsByClassName('modal');
		return modals.length && modals[modals.length - 1] === modalElement;
	};

	// llens: Svelte 5 では {#if} ブロックの teardown が先に DOM を外すことがあり、
	// 無条件 removeChild は NotFoundError で effect を中断させる(EditUserModal が
	// 空欄になる実害)。親子関係を確認してから外す。
	const removeModalFromBody = () => {
		if (modalElement && modalElement.parentNode === document.body) {
			document.body.removeChild(modalElement);
		}
	};

	$effect(() => {
		if (show && modalElement) {
			document.body.appendChild(modalElement);
			window.addEventListener('keydown', handleKeyDown);
			document.body.style.overflow = 'hidden';
		} else if (modalElement) {
			window.removeEventListener('keydown', handleKeyDown);
			removeModalFromBody();
			document.body.style.overflow = 'unset';
		}
	});

	onDestroy(() => {
		show = false;
		window.removeEventListener('keydown', handleKeyDown);
		removeModalFromBody();
		document.body.style.overflow = 'unset';
	});
</script>

{#if show}
	<!-- svelte-ignore a11y_click_events_have_key_events -->
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		bind:this={modalElement}
		class="modal fixed top-0 right-0 left-0 bottom-0 bg-black/60 w-full h-screen max-h-[100dvh] {containerClassName} flex justify-center z-9999 overflow-y-auto overscroll-contain"
		in:fade={{ duration: 10 }}
		onmousedown={() => {
			show = false;
		}}
	>
		<div
			class="m-auto max-w-full {sizeToWidth(size)} {size !== 'full'
				? 'mx-2'
				: ''} shadow-3xl min-h-fit scrollbar-hidden {className}"
			in:flyAndScale
			onmousedown={(e) => {
				e.stopPropagation();
			}}
		>
			{@render children?.()}
		</div>
	</div>
{/if}
