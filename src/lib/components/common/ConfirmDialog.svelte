<script lang="ts">
	import { onDestroy, getContext, type Snippet } from 'svelte';
	const i18n = getContext('i18n');

	import { fade } from 'svelte/transition';
	import { flyAndScale } from '$lib/utils/transitions';

	let {
		show = $bindable(false),
		title = '',
		message = '',
		cancelLabel = $i18n.t('Cancel'),
		confirmLabel = $i18n.t('Confirm'),
		input = false,
		inputPlaceholder = '',
		inputValue = $bindable(''),
		onConfirm = undefined,
		onCancel = undefined,
		children
	}: {
		show?: boolean;
		title?: string;
		message?: string;
		cancelLabel?: string;
		confirmLabel?: string;
		input?: boolean;
		inputPlaceholder?: string;
		inputValue?: string;
		onConfirm?: (inputValue: string) => void | Promise<void>;
		onCancel?: () => void;
		children?: Snippet;
	} = $props();

	let modalElement: HTMLDivElement | null = $state(null);

	const handleKeyDown = (event: KeyboardEvent) => {
		if (event.key === 'Escape') {
			show = false;
		}

		if (event.key === 'Enter') {
			confirmHandler();
		}
	};

	const confirmHandler = async () => {
		show = false;
		await onConfirm?.(inputValue);
	};

	// llens: Modal.svelte と同じく、{#if} teardown が先に DOM を外した後の
	// 無条件 removeChild は NotFoundError になるので親子関係を確認してから外す。
	const removeModalFromBody = () => {
		if (modalElement && modalElement.parentNode === document.body) {
			document.body.removeChild(modalElement);
		}
	};

	// body のスクロール止めは、body 直下に portal 中の data-scroll-lock が 1 つも残って
	// いないときだけ解く (common/Modal.svelte と同じ。重ねたモーダルの下を守る)
	const releaseScrollLock = () => {
		if (!document.body.querySelector(':scope > [data-scroll-lock]')) {
			document.body.style.overflow = 'unset';
		}
	};

	// 後始末は cleanup で返す: 閉じるとき {#if} が先に要素を外して modalElement が null に
	// なってから effect が走るため、else 分岐では keydown が外れず、閉じた後の Enter が
	// 最後の onConfirm (削除・上書きアップロード等) を再実行していた (#150 の UI 確認で発覚)
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
		class=" fixed top-0 right-0 left-0 bottom-0 bg-black/60 w-full h-screen max-h-[100dvh] flex justify-center z-99999999 overflow-hidden overscroll-contain"
		in:fade={{ duration: 10 }}
		onmousedown={() => {
			show = false;
		}}
	>
		<div
			class=" m-auto rounded-2xl max-w-full w-[32rem] mx-2 bg-white dark:bg-gray-900 max-h-[100dvh] shadow-3xl"
			in:flyAndScale
			onmousedown={(e) => {
				e.stopPropagation();
			}}
		>
			<div class="px-[1.75rem] py-6 flex flex-col">
				<div class=" text-lg font-semibold text-content mb-2.5">
					{#if title !== ''}
						{title}
					{:else}
						{$i18n.t('Confirm your action')}
					{/if}
				</div>

				{#if children}
					{@render children()}
				{:else}
					<div class=" text-sm text-content-muted flex-1">
						{#if message !== ''}
							{message}
						{:else}
							{$i18n.t('This action cannot be undone. Do you wish to continue?')}
						{/if}

						{#if input}
							<textarea
								bind:value={inputValue}
								placeholder={inputPlaceholder ? inputPlaceholder : $i18n.t('Enter your message')}
								class="w-full mt-2 rounded-lg px-4 py-2 text-sm text-content bg-surface-muted outline-hidden resize-none"
								rows="3"
								required
							></textarea>
						{/if}
					</div>
				{/if}

				<div class="mt-6 flex justify-between gap-1.5">
					<button
						class="bg-secondary hover:bg-secondary-hover text-secondary-content font-medium w-full py-2.5 rounded-lg transition"
						onclick={() => {
							show = false;
							onCancel?.();
						}}
						type="button"
					>
						{cancelLabel}
					</button>
					<button
						class="bg-primary hover:bg-primary-hover text-primary-content font-medium w-full py-2.5 rounded-lg transition"
						onclick={() => {
							confirmHandler();
						}}
						type="button"
					>
						{confirmLabel}
					</button>
				</div>
			</div>
		</div>
	</div>
{/if}
