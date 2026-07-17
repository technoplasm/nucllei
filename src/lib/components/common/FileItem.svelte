<script lang="ts">
	import { getContext } from 'svelte';
	import { formatFileSize } from '$lib/utils';

	import FileItemModal from './FileItemModal.svelte';
	import Spinner from './Spinner.svelte';
	import Tooltip from './Tooltip.svelte';

	const i18n = getContext('i18n');

	let {
		className = 'w-60',
		colorClassName = 'bg-white dark:bg-gray-850 border border-gray-50 dark:border-white/5',
		url = null,
		dismissible = false,
		loading = false,
		item = $bindable(null),
		small = false,
		name,
		type,
		size,
		onClick = undefined,
		onDismiss = undefined
	}: {
		className?: string;
		colorClassName?: string;
		url?: string | null;
		dismissible?: boolean;
		loading?: boolean;
		item?: any;
		small?: boolean;
		name: string;
		type: string;
		size?: number;
		onClick?: () => void;
		onDismiss?: () => void | Promise<void>;
	} = $props();

	let showModal = $state(false);

	const decodeString = (str: string) => {
		try {
			return decodeURIComponent(str);
		} catch (e) {
			return str;
		}
	};
</script>

{#if item}
	<FileItemModal bind:show={showModal} bind:item />
{/if}

<!-- llens: dismiss ボタンを本体 button の兄弟に出すため root は div(button 入れ子は HTML 違反) -->
<div class="relative group {className}">
	<button
		class="w-full p-1.5 flex items-center gap-1 {colorClassName} {small
			? 'rounded-xl'
			: 'rounded-2xl'} text-left"
		type="button"
		onclick={async () => {
			if (item?.file?.data) {
				// llens(#52): content が無くても(未抽出/失敗)状態を見せるためモーダルを開く。
				showModal = !showModal;
			} else {
				if (url) {
					if (type === 'file') {
						window.open(`${url}/content`, '_blank').focus();
					} else {
						window.open(`${url}`, '_blank').focus();
					}
				}
			}

			onClick?.();
		}}
	>
		{#if !small}
			<div class="p-3 bg-black/20 dark:bg-white/10 text-white rounded-xl">
				{#if !loading}
					<span class="i-lucide-file-text size-5 block"></span>
				{:else}
					<Spinner />
				{/if}
			</div>
		{/if}

		{#if !small}
			<div class="flex flex-col justify-center -space-y-0.5 px-2.5 w-full">
				<div class=" text-content text-sm font-medium line-clamp-1 mb-1">
					{decodeString(name)}
				</div>

				<div class=" flex justify-between text-content-muted text-xs line-clamp-1">
					{#if type === 'file'}
						{$i18n.t('File')}
					{:else if type === 'doc'}
						{$i18n.t('Document')}
					{:else if type === 'collection'}
						{$i18n.t('Collection')}
					{:else}
						<span class=" capitalize line-clamp-1">{type}</span>
					{/if}
					{#if size}
						<span class="capitalize">{formatFileSize(size)}</span>
					{/if}
				</div>
			</div>
		{:else}
			<Tooltip content={decodeString(name)} className="flex flex-col w-full" placement="top-start">
				<div class="flex flex-col justify-center -space-y-0.5 px-2.5 w-full">
					<div class=" text-content text-sm flex justify-between items-center">
						{#if loading}
							<div class=" shrink-0 mr-2">
								<Spinner className="size-4" />
							</div>
						{/if}
						<div class="font-medium line-clamp-1 flex-1">{decodeString(name)}</div>
						<div class="text-content-muted text-xs capitalize shrink-0">{formatFileSize(size)}</div>
					</div>
				</div>
			</Tooltip>
		{/if}
	</button>

	{#if dismissible}
		<div class=" absolute -top-1 -right-1">
			<button
				class=" bg-white text-black border border-gray-50 rounded-full group-hover:visible invisible transition"
				type="button"
				aria-label={$i18n.t('Remove')}
				onclick={(e) => {
					e.stopPropagation();
					onDismiss?.();
				}}
			>
				<span class="i-lucide-x size-4 block"></span>
			</button>
		</div>
	{/if}
</div>
