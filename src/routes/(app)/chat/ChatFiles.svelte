<script lang="ts">
	import { getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import { showControls } from '$lib/stores';
	import { formatFileSize } from '$lib/utils';
	import { getFileById } from '$lib/apis/files';
	import type { ChatFile, ChatHistory } from '$lib/types/chat';

	import FileItemModal from '$lib/components/common/FileItemModal.svelte';

	// llens(#69): 右ペイン「このチャットのファイル」。
	// chatFiles(type 'file')+ history 全メッセージの画像(id 付き = file 化済み)を
	// file.id で dedupe して一覧。クリックで getFileById(data 込み)→ FileItemModal。
	// 旧チャットの data: URL 画像は id が無いため対象外(従来どおりメッセージ内でのみ表示)。
	const i18n = getContext('i18n');

	let { history = null, chatFiles = [] }: { history?: ChatHistory | null; chatFiles?: ChatFile[] } =
		$props();

	type FileListItem = { id: string; type: 'file' | 'image'; name: string; size: number | null };

	const collectFiles = (chatFiles: ChatFile[] | null, history: ChatHistory | null) => {
		const items: FileListItem[] = [];
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

	const items = $derived(collectFiles(chatFiles, history));

	let showFileModal = $state(false);
	let selectedItem = $state<any>(null);

	const openFile = async (item: FileListItem) => {
		// #94: 削除済み file は owner には縮小 marker {id, deleted_at}(meta/filename 無し)、
		// owner 以外は 404 で返る。404 でもモーダルは snapshot で開き、削除済み表示は
		// FileItemModal 側(refetch 404 → 削除済み)に任せる。表示名はチャット側の名前で補う
		let notFound = false;
		const res = await getFileById(item.id).catch((err) => {
			if (err?.status === 404) {
				notFound = true;
			} else {
				toast.error(`${err?.message ?? err}`);
			}
			return null;
		});
		if (!res && !notFound) return;

		selectedItem = {
			id: item.id,
			name: res?.meta?.name ?? res?.filename ?? item.name,
			size: res?.meta?.size ?? item.size,
			file: res
		};
		showFileModal = true;
	};
</script>

{#if selectedItem}
	<FileItemModal bind:show={showFileModal} item={selectedItem} download={true} />
{/if}

<div class="flex flex-col w-full">
	<div class="flex items-center justify-between mb-1.5">
		<div class="text-sm font-medium text-content">
			{$i18n.t('Files in this chat')}
		</div>
		<button
			class="flex p-1 rounded-lg text-content-muted hover:text-content hover:bg-surface-hover"
			type="button"
			aria-label={$i18n.t('Close')}
			onclick={() => {
				showControls.set(false);
			}}
		>
			<span class="i-lucide-x size-4 block"></span>
		</button>
	</div>

	{#if items.length === 0}
		<div class="text-xs text-content-subtle py-4 text-center">
			{$i18n.t('No files in this chat')}
		</div>
	{:else}
		<div class="flex flex-col gap-0.5">
			{#each items as item (item.id)}
				<button
					class="flex items-center gap-3 w-full px-2 py-1.5 rounded-xl text-left hover:bg-surface-hover"
					type="button"
					onclick={() => openFile(item)}
				>
					{#if item.type === 'image'}
						<img
							src={`${WEBUI_API_BASE_URL}/files/${item.id}/content`}
							alt={item.name}
							loading="lazy"
							class="size-9 shrink-0 rounded-lg object-cover bg-surface-muted"
						/>
					{:else}
						<div
							class="size-9 shrink-0 rounded-lg bg-surface-muted flex items-center justify-center text-content-muted"
						>
							<span class="i-lucide-file size-4 block"></span>
						</div>
					{/if}

					<div class="flex-1 min-w-0 self-center">
						<div class="text-sm font-medium line-clamp-1 text-content">{item.name}</div>
						{#if item.size}
							<div class="text-xs text-content-muted line-clamp-1 mt-0.5">
								{formatFileSize(item.size)}
							</div>
						{/if}
					</div>
				</button>
			{/each}
		</div>
	{/if}
</div>
