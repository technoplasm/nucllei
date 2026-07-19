<script lang="ts">
	import { Pane, PaneResizer } from 'paneforge';

	import { onDestroy, onMount, tick } from 'svelte';
	import { showControls, showCallOverlay } from '$lib/stores';

	import CallOverlay from './MessageInput/CallOverlay.svelte';
	import ChatFiles from './ChatFiles.svelte';
	import Drawer from '$lib/components/common/Drawer.svelte';

	let {
		history = $bindable(),
		chatId = null,
		chatFiles = $bindable([]),
		eventTarget,
		submitPrompt,
		stopResponse,
		files = $bindable(),
		modelId = null,
		pane = $bindable()
	}: {
		history: any;
		chatId?: string | null;
		chatFiles?: any[];
		eventTarget: EventTarget;
		submitPrompt: Function;
		stopResponse: Function;
		files: any[];
		modelId?: string | null;
		pane?: any;
	} = $props();

	let mediaQuery: MediaQueryList;
	let largeScreen = $state(false);

	let minSize = $state(0);

	export const openPane = () => {
		if (parseInt(localStorage?.chatControlsSize)) {
			pane.resize(parseInt(localStorage?.chatControlsSize));
		} else {
			pane.resize(minSize);
		}
	};

	const handleMediaQuery = async (e: { matches: boolean }) => {
		largeScreen = e.matches;
		if (!e.matches) {
			pane = null;
		}

		// 画面区分が変わったら CallOverlay を出し直す(Drawer ⇄ Pane の載せ替え)
		if ($showCallOverlay) {
			showCallOverlay.set(false);
			await tick();
			showCallOverlay.set(true);
		}
	};

	onMount(() => {
		// listen to resize 1024px
		mediaQuery = window.matchMedia('(min-width: 1024px)');

		mediaQuery.addEventListener('change', handleMediaQuery);
		handleMediaQuery(mediaQuery);

		const container = document.getElementById('chat-container');

		if (container) {
			// initialize the minSize based on the container width
			minSize = Math.floor((350 / container.clientWidth) * 100);

			const resizeObserver = new ResizeObserver((entries) => {
				for (let entry of entries) {
					const width = entry.contentRect.width;
					const percentage = (350 / width) * 100;
					// set the minSize to the percentage, must be an integer
					minSize = Math.floor(percentage);

					if ($showControls) {
						if (pane && pane.isExpanded() && pane.getSize() < minSize) {
							pane.resize(minSize);
						}
					}
				}
			});

			resizeObserver.observe(container);
		}
	});

	onDestroy(() => {
		showControls.set(false);

		mediaQuery.removeEventListener('change', handleMediaQuery);
	});

	const closeHandler = () => {
		showControls.set(false);

		if ($showCallOverlay) {
			showCallOverlay.set(false);
		}
	};

	$effect(() => {
		if (!chatId) {
			closeHandler();
		}
	});
</script>

{#if !largeScreen}
	{#if $showControls}
		<Drawer
			show={$showControls}
			onClose={() => {
				showControls.set(false);
			}}
		>
			<div class=" {$showCallOverlay ? ' h-screen  w-full' : 'px-6 py-4'} h-full">
				{#if $showCallOverlay}
					<div
						class=" h-full max-h-[100dvh] bg-white text-content dark:bg-black flex justify-center"
					>
						<CallOverlay
							bind:files
							{submitPrompt}
							{stopResponse}
							{modelId}
							{eventTarget}
							onClose={() => {
								showControls.set(false);
							}}
						/>
					</div>
				{:else}
					<!-- llens(#69): CallOverlay 以外はこのチャットのファイル一覧 -->
					<ChatFiles {history} {chatFiles} />
				{/if}
			</div>
		</Drawer>
	{/if}
{:else}
	{#if $showControls}
		<PaneResizer class="relative flex w-2 items-center justify-center bg-background group">
			<div class="z-10 flex h-7 w-5 items-center justify-center rounded-xs">
				<span class="i-lucide-ellipsis-vertical size-4 block invisible group-hover:visible"></span>
			</div>
		</PaneResizer>
	{/if}

	<Pane
		bind:pane
		defaultSize={0}
		onResize={(size) => {
			if ($showControls && pane.isExpanded()) {
				if (size < minSize) {
					pane.resize(minSize);
				}

				if (size < minSize) {
					localStorage.chatControlsSize = 0;
				} else {
					localStorage.chatControlsSize = size;
				}
			}
		}}
		onCollapse={() => {
			showControls.set(false);
		}}
		collapsible={true}
		class=" z-10 "
	>
		{#if $showControls}
			<div class="flex max-h-full min-h-full">
				<div
					class="w-full px-4 py-4 bg-surface-raised border border-line z-40 pointer-events-auto overflow-y-auto"
				>
					{#if $showCallOverlay}
						<div class="w-full h-full flex justify-center">
							<CallOverlay
								bind:files
								{submitPrompt}
								{stopResponse}
								{modelId}
								{eventTarget}
								onClose={() => {
									showControls.set(false);
								}}
							/>
						</div>
					{:else}
						<!-- llens(#69): CallOverlay 以外はこのチャットのファイル一覧 -->
						<ChatFiles {history} {chatFiles} />
					{/if}
				</div>
			</div>
		{/if}
	</Pane>
{/if}
