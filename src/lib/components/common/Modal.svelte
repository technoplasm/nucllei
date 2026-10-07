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

	// body のスクロール止めは、body 直下に portal 中の data-scroll-lock (Modal / Drawer /
	// ConfirmDialog / ImagePreview) が 1 つも残っていないときだけ解く — 重ねたモーダルの
	// 上だけ閉じたとき、下が開いたままなのにページがスクロールできるようにならないため
	const releaseScrollLock = () => {
		if (!document.body.querySelector(':scope > [data-scroll-lock]')) {
			document.body.style.overflow = 'unset';
		}
	};

	// 後始末は cleanup で返す: 閉じるとき {#if} が先に要素を外して modalElement が null に
	// なってから effect が走るため、else 分岐では keydown が外れず body の overflow: hidden も
	// 残っていた (ConfirmDialog b0cbbb571 と同じ不具合)。要素はローカルに取ってから使う
	$effect(() => {
		const el = modalElement;
		if (!(show && el)) return;
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
		removeModalFromBody();
		releaseScrollLock();
	});
</script>

{#if show}
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		bind:this={modalElement}
		data-scroll-lock
		class="modal fixed top-0 right-0 left-0 bottom-0 bg-black/60 w-full h-screen max-h-[100dvh] {containerClassName} flex justify-center z-9999 overflow-y-auto overscroll-contain"
		in:fade={{ duration: 10 }}
		onmousedown={() => {
			show = false;
		}}
	>
		<div
			class="m-auto max-w-full {sizeToWidth(size)} {size !== 'full'
				? 'mx-2'
				: ''} shadow-3xl min-h-fit {className}"
			in:flyAndScale
			onmousedown={(e) => {
				e.stopPropagation();
			}}
		>
			{@render children?.()}
		</div>
	</div>
{/if}
