<script lang="ts">
	import { DropdownMenu } from 'bits-ui';
	import { flyAndScale } from '$lib/utils/transitions';
	import { getContext, type Snippet } from 'svelte';

	const i18n = getContext('i18n');

	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	let {
		onRename = undefined,
		onExport = undefined,
		onDelete = undefined,
		children
	}: {
		onRename?: () => void;
		onExport?: () => void;
		onDelete?: () => void;
		children?: Snippet;
	} = $props();

	let show = $state(false);
</script>

<Dropdown bind:show>
	<Tooltip content={$i18n.t('More')}>
		{@render children?.()}
	</Tooltip>

	{#snippet content()}
		<DropdownMenu.Content
			class="w-full max-w-[160px] rounded-lg px-1 py-1.5  z-50 bg-white dark:bg-gray-850 dark:text-white shadow-lg"
			sideOffset={-2}
			side="bottom"
			align="start"
			transition={flyAndScale}
		>
			<DropdownMenu.Item
				class="flex gap-2 items-center px-3 py-1.5 text-sm  cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-md"
				on:click={() => {
					onRename?.();
				}}
			>
				<span class="i-lucide-pencil size-4 block"></span>
				<div class="flex items-center">{$i18n.t('Rename')}</div>
			</DropdownMenu.Item>

			<DropdownMenu.Item
				class="flex gap-2 items-center px-3 py-1.5 text-sm  cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-md"
				on:click={() => {
					onExport?.();
				}}
			>
				<span class="i-lucide-download size-4 block"></span>

				<div class="flex items-center">{$i18n.t('Export')}</div>
			</DropdownMenu.Item>

			<DropdownMenu.Item
				class="flex  gap-2  items-center px-3 py-1.5 text-sm  cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-md"
				on:click={() => {
					onDelete?.();
				}}
			>
				<span class="i-lucide-trash-2 size-4 block"></span>
				<div class="flex items-center">{$i18n.t('Delete')}</div>
			</DropdownMenu.Item>
		</DropdownMenu.Content>
	{/snippet}
</Dropdown>
