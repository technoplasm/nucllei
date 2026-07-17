<script lang="ts">
	import { onMount, onDestroy, type Snippet } from 'svelte';

	import Collapsible from './Collapsible.svelte';
	import Tooltip from './Tooltip.svelte';

	let {
		open = $bindable(true),
		name = '',
		collapsible = true,
		onAddLabel = '',
		onAdd = null,
		dragAndDrop = true,
		className = '',
		onChange = undefined,
		onDrop = undefined,
		onImport = undefined,
		children
	}: {
		open?: boolean;
		name?: string;
		collapsible?: boolean;
		onAddLabel?: string;
		onAdd?: null | (() => void);
		dragAndDrop?: boolean;
		className?: string;
		onChange?: (open: boolean) => void;
		onDrop?: (data: any) => void;
		onImport?: (content: any) => void;
		children?: Snippet;
	} = $props();

	let folderElement: HTMLDivElement | null = $state(null);

	let draggedOver = $state(false);

	const onDragOver = (e: DragEvent) => {
		e.preventDefault();
		e.stopPropagation();
		draggedOver = true;
	};

	const dropHandler = (e: DragEvent) => {
		e.preventDefault();
		e.stopPropagation();

		if (e.target instanceof Node && folderElement?.contains(e.target)) {
			if (e.dataTransfer?.items && e.dataTransfer.items.length > 0) {
				// Iterate over all items in the DataTransferItemList use functional programming
				for (const item of Array.from(e.dataTransfer.items)) {
					// If dropped items aren't files, reject them
					if (item.kind === 'file') {
						const file = item.getAsFile();
						if (file && file.type === 'application/json') {
							// Read the JSON file with FileReader
							const reader = new FileReader();
							reader.onload = async function (event) {
								// readAsText の結果は常に string(型ガードのみ — 挙動不変)
								const result = event.target?.result;
								if (typeof result !== 'string') {
									return;
								}
								try {
									const fileContent = JSON.parse(result);
									open = true;
									onImport?.(fileContent);
								} catch (error) {
									console.error('Error parsing JSON file:', error);
								}
							};

							// Start reading the file
							reader.readAsText(file);
						} else {
							console.error('Only JSON file types are supported.');
						}
					} else {
						open = true;

						const dataTransfer = e.dataTransfer.getData('text/plain');
						const data = JSON.parse(dataTransfer);

						onDrop?.(data);
					}
				}
			}

			draggedOver = false;
		}
	};

	const onDragLeave = (e: DragEvent) => {
		e.preventDefault();
		e.stopPropagation();

		draggedOver = false;
	};

	onMount(() => {
		if (!dragAndDrop) {
			return;
		}
		folderElement?.addEventListener('dragover', onDragOver);
		folderElement?.addEventListener('drop', dropHandler);
		folderElement?.addEventListener('dragleave', onDragLeave);
	});

	onDestroy(() => {
		if (!dragAndDrop) {
			return;
		}
		// llens: 旧実装は destroy 時に dragover を addEventListener していた(明白なバグ)。remove に修正。
		folderElement?.removeEventListener('dragover', onDragOver);
		folderElement?.removeEventListener('drop', dropHandler);
		folderElement?.removeEventListener('dragleave', onDragLeave);
	});
</script>

<div bind:this={folderElement} class="relative {className}">
	{#if draggedOver}
		<div
			class="absolute top-0 left-0 w-full h-full rounded-xs bg-gray-100/50 dark:bg-gray-700/20 bg-opacity-50 dark:bg-opacity-10 z-50 pointer-events-none touch-none"
		></div>
	{/if}

	{#if collapsible}
		<Collapsible bind:open className="w-full " buttonClassName="w-full" {onChange}>
			<!-- svelte-ignore a11y_no_static_element_interactions -->
			<div
				class="w-full group rounded-md relative flex items-center justify-between hover:bg-gray-100 dark:hover:bg-gray-900 text-content-muted transition"
			>
				<button class="w-full py-1.5 pl-2 flex items-center gap-1.5 text-xs font-medium">
					<div class="text-content-subtle">
						{#if open}
							<span class="i-lucide-chevron-down size-3 block"></span>
						{:else}
							<span class="i-lucide-chevron-right size-3 block"></span>
						{/if}
					</div>

					<div class="translate-y-[0.5px]">
						{name}
					</div>
				</button>

				{#if onAdd}
					<button
						class="absolute z-10 right-2 invisible group-hover:visible self-center flex items-center text-content"
						onpointerup={(e) => {
							e.stopPropagation();
						}}
						onclick={(e) => {
							e.stopPropagation();
							onAdd?.();
						}}
					>
						<Tooltip content={onAddLabel}>
							<button
								class="p-0.5 dark:hover:bg-gray-850 rounded-lg touch-auto"
								aria-label={onAddLabel}
							>
								<span class="i-lucide-plus size-3 block"></span>
							</button>
						</Tooltip>
					</button>
				{/if}
			</div>

			{#snippet content()}
				<div class="w-full">
					{@render children?.()}
				</div>
			{/snippet}
		</Collapsible>
	{:else}
		{@render children?.()}
	{/if}
</div>
