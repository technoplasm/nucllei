<script lang="ts">
	import { DropdownMenu } from 'bits-ui';
	import { flyAndScale } from '$lib/utils/transitions';
	import { getContext, type Snippet } from 'svelte';

	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	const i18n = getContext('i18n');

	let {
		model,
		exportHandler,
		hideHandler,
		onClose = () => {},
		children
	}: {
		model: any;
		exportHandler: () => void;
		hideHandler: () => void;
		onClose?: () => void;
		children?: Snippet;
	} = $props();

	let show = $state(false);
</script>

<Dropdown
	bind:show
	onChange={(state) => {
		if (state === false) {
			onClose();
		}
	}}
>
	<Tooltip content={$i18n.t('More')}>
		{@render children?.()}
	</Tooltip>

	{#snippet content()}
		<DropdownMenu.Content
			class="w-full max-w-[160px] rounded-xl px-1 py-1.5 border border-line z-50 bg-surface-raised text-content shadow-sm"
			sideOffset={-2}
			side="bottom"
			align="start"
			transition={flyAndScale}
		>
			<DropdownMenu.Item
				class="flex  gap-2  items-center px-3 py-2 text-sm  font-medium cursor-pointer hover:bg-surface-hover rounded-md"
				on:click={() => {
					hideHandler();
				}}
			>
				{#if model?.meta?.hidden ?? false}
					<span class="i-lucide-eye-off size-4 block"></span>
				{:else}
					<span class="i-lucide-eye size-4 block"></span>
				{/if}

				<div class="flex items-center">
					{#if model?.meta?.hidden ?? false}
						{$i18n.t('Show Model')}
					{:else}
						{$i18n.t('Hide Model')}
					{/if}
				</div>
			</DropdownMenu.Item>

			<DropdownMenu.Item
				class="flex gap-2 items-center px-3 py-2 text-sm  font-medium cursor-pointer hover:bg-surface-hover rounded-md"
				on:click={() => {
					exportHandler();
				}}
			>
				<span class="i-lucide-download size-4 block"></span>

				<div class="flex items-center">{$i18n.t('Export')}</div>
			</DropdownMenu.Item>
		</DropdownMenu.Content>
	{/snippet}
</Dropdown>
