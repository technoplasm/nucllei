<script lang="ts">
	import { getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import { formatFileSize } from '$lib/utils';
	import { getFileById } from '$lib/apis/files';

	import FileItemModal from '$lib/components/common/FileItemModal.svelte';
	import Document from '$lib/components/icons/Document.svelte';

	// llens(#69): 右ペイン「このチャットのファイル」。
	// chatFiles(type 'file')+ history 全メッセージの画像(id 付き = file 化済み)を
	// file.id で dedupe して一覧。クリックで getFileById(data 込み)→ FileItemModal。
	// 旧チャットの data: URL 画像は id が無いため対象外(従来どおりメッセージ内でのみ表示)。
	const i18n = getContext('i18n');

	export let history = null;
	export let chatFiles = [];

	const collectFiles = (chatFiles, history) => {
		const items = [];
		const seen = new Set();

		for (const file of chatFiles ?? []) {
			if (file?.type !== 'file' || !file?.id || seen.has(file.id)) {
				continue;
			}
			seen.add(file.id);
			items.push({
				id: file.id,
				type: 'file',
				name: file?.name ?? file?.file?.meta?.name ?? file?.file?.filename ?? file.id,
				size: file?.size ?? file?.file?.meta?.size ?? null
			});
		}

		for (const message of Object.values(history?.messages ?? {})) {
			for (const file of message?.files ?? []) {
				if (file?.type !== 'image' || !file?.id || seen.has(file.id)) {
					continue;
				}
				seen.add(file.id);
				items.push({
					id: file.id,
					type: 'image',
					name: file?.name ?? file.id,
					size: file?.size ?? null
				});
			}
		}

		return items;
	};

	$: items = collectFiles(chatFiles, history);

	let showFileModal = false;
	let selectedItem = null;

	const openFile = async (item) => {
		const res = await getFileById(item.id).catch((err) => {
			toast.error(`${err}`);
			return null;
		});
		if (!res) return;

		selectedItem = {
			id: res.id,
			name: res?.meta?.name ?? res?.filename,
			size: res?.meta?.size,
			file: res
		};
		showFileModal = true;
	};
</script>

{#if selectedItem}
	<FileItemModal bind:show={showFileModal} item={selectedItem} download={true} />
{/if}

<div class="flex flex-col w-full">
	<div class="text-sm font-medium dark:text-gray-100 mb-1.5">
		{$i18n.t('Files in this chat')}
	</div>

	{#if items.length === 0}
		<div class="text-xs text-gray-500 py-4 text-center">
			{$i18n.t('No files in this chat')}
		</div>
	{:else}
		<div class="flex flex-col gap-0.5">
			{#each items as item (item.id)}
				<button
					class="flex items-center gap-3 w-full px-2 py-1.5 rounded-xl text-left hover:bg-black/5 dark:hover:bg-white/5 transition"
					type="button"
					on:click={() => openFile(item)}
				>
					{#if item.type === 'image'}
						<img
							src={`${WEBUI_API_BASE_URL}/files/${item.id}/content`}
							alt={item.name}
							loading="lazy"
							class="size-9 shrink-0 rounded-lg object-cover bg-black/5 dark:bg-white/5"
						/>
					{:else}
						<div
							class="size-9 shrink-0 rounded-lg bg-black/5 dark:bg-white/5 flex items-center justify-center text-gray-500"
						>
							<Document className="size-4" />
						</div>
					{/if}

					<div class="flex-1 min-w-0 self-center">
						<div class="text-sm font-medium line-clamp-1 dark:text-gray-100">{item.name}</div>
						{#if item.size}
							<div class="text-xs text-gray-500 line-clamp-1 mt-0.5">
								{formatFileSize(item.size)}
							</div>
						{/if}
					</div>
				</button>
			{/each}
		</div>
	{/if}
</div>
