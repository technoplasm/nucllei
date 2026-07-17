<script lang="ts">
	import { DropdownMenu } from 'bits-ui';
	import { flyAndScale } from '$lib/utils/transitions';
	import { getContext } from 'svelte';

	import { user } from '$lib/stores';

	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	const i18n = getContext('i18n');

	let {
		uploadFilesHandler,
		// llens(#69): 保存済みファイル(/files に保存済みの file)の再添付 picker を開く
		openLibraryHandler = () => {},
		onClose,
		children
	}: {
		uploadFilesHandler: Function;
		openLibraryHandler?: Function;
		onClose: Function;
		children?: any;
	} = $props();

	let show = $state(false);

	const fileUploadEnabled = $derived(
		$user?.role === 'admin' || $user?.permissions?.chat?.file_upload
	);
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
			class="w-full max-w-[200px] rounded-xl px-1 py-1 border border-gray-300/30 dark:border-gray-700/50 z-50 bg-white dark:bg-gray-850 dark:text-white shadow-sm"
			sideOffset={10}
			alignOffset={-8}
			side="top"
			align="start"
			transition={flyAndScale}
		>
			<Tooltip
				content={!fileUploadEnabled ? $i18n.t('You do not have permission to upload files') : ''}
				className="w-full"
			>
				<DropdownMenu.Item
					class="flex gap-2 items-center px-3 py-2 text-sm font-medium cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl {!fileUploadEnabled
						? 'opacity-50'
						: ''}"
					on:click={() => {
						if (fileUploadEnabled) {
							uploadFilesHandler();
						}
					}}
				>
					<span class="i-lucide-file-up size-4 block"></span>
					<div class="line-clamp-1">{$i18n.t('Upload Files')}</div>
				</DropdownMenu.Item>
			</Tooltip>

			<Tooltip
				content={!fileUploadEnabled ? $i18n.t('You do not have permission to upload files') : ''}
				className="w-full"
			>
				<DropdownMenu.Item
					class="flex gap-2 items-center px-3 py-2 text-sm font-medium cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl {!fileUploadEnabled
						? 'opacity-50'
						: ''}"
					on:click={() => {
						if (fileUploadEnabled) {
							openLibraryHandler();
						}
					}}
				>
					<span class="i-lucide-folder-open size-4 block"></span>
					<div class="line-clamp-1">{$i18n.t('Saved Files')}</div>
				</DropdownMenu.Item>
			</Tooltip>
		</DropdownMenu.Content>
	{/snippet}
</Dropdown>
