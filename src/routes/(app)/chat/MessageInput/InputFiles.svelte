<script lang="ts">
	// 入力欄の添付ストリップ(#70 C トラック: MessageInput.svelte 分割)。
	// 画像はプレビュー + vision 非対応警告、それ以外は FileItem。解除は onDismiss で親へ
	import { getContext } from 'svelte';

	import { type Model } from '$lib/stores';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import FileItem from '$lib/components/common/FileItem.svelte';
	import Image from '$lib/components/common/Image.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	const i18n = getContext('i18n');

	let {
		files,
		atSelectedModel,
		selectedModels,
		visionCapableModels,
		onDismiss
	}: {
		files: any[];
		atSelectedModel: Model | undefined;
		selectedModels: string[];
		visionCapableModels: (string | Model)[];
		onDismiss: (fileIdx: number) => Promise<void> | void;
	} = $props();
</script>

<div class="mx-2 mt-2.5 -mb-1 flex items-center flex-wrap gap-2">
	{#each files as file, fileIdx}
		{#if file.type === 'image'}
			<div class=" relative group">
				<div class="relative flex items-center">
					<Image src={file.url} alt="input" imageClassName=" size-14 rounded-xl object-cover" />
					{#if file.status === 'uploading'}
						<div
							class="absolute inset-0 flex items-center justify-center bg-black/20 rounded-xl pointer-events-none"
						>
							<Spinner className="size-4" />
						</div>
					{/if}
					{#if atSelectedModel ? visionCapableModels.length === 0 : selectedModels.length !== visionCapableModels.length}
						<Tooltip
							className=" absolute top-1 left-1"
							content={[...(atSelectedModel ? [atSelectedModel] : selectedModels)]
								.filter((id) => !visionCapableModels.includes(id))
								.join(', ')}
						>
							<span class="i-lucide-triangle-alert size-4 block text-yellow-300"></span>
						</Tooltip>
					{/if}
				</div>
				<div class=" absolute -top-1 -right-1">
					<button
						class=" bg-white text-black border border-white rounded-full group-hover:visible invisible"
						type="button"
						aria-label={$i18n.t('Remove file')}
						onclick={async () => {
							await onDismiss(fileIdx);
						}}
					>
						<span class="i-lucide-x size-4 block"></span>
					</button>
				</div>
			</div>
		{:else}
			<FileItem
				item={file}
				name={file.name}
				type={file.type}
				size={file?.size}
				loading={file.status === 'uploading'}
				dismissible={true}
				onDismiss={async () => {
					await onDismiss(fileIdx);
				}}
			/>
		{/if}
	{/each}
</div>
