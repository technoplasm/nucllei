<script lang="ts">
	import { getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import { showControls } from '$lib/stores';
	import { formatFileSize } from '$lib/utils';
	import { getFileById } from '$lib/apis/files';
	import { collectChatFiles, type ChatFileListItem } from '$lib/utils/chat-files';
	import type { ChatFile, ChatHistory } from '$lib/types/chat';

	import FileItemModal from '$lib/components/common/FileItemModal.svelte';

	// llens(#69): 右ペイン「このチャットのファイル」。
	// 収集(chat レベル添付 + メッセージの画像・tool 生成ファイル、file.id dedupe)は
	// collectChatFiles に一元化 — Navbar のバッジ数と同じ集合を見せる。
	// クリックで getFileById(data 込み)→ FileItemModal。
	const i18n = getContext('i18n');

	// reserveTopRight: 画面右上に接する置き方(大画面の右ペイン)のとき、見出しの右端に
	// 残り時間リング(IdleTimeoutRing)の分を空ける。下から出る Drawer はリングより手前なので不要
	let {
		history = null,
		chatFiles = [],
		reserveTopRight = false
	}: { history?: ChatHistory | null; chatFiles?: ChatFile[]; reserveTopRight?: boolean } = $props();

	const items = $derived(collectChatFiles(chatFiles, history));

	let showFileModal = $state(false);
	let selectedItem = $state<any>(null);

	const openFile = async (item: ChatFileListItem) => {
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
	<!-- reserveTopRight: 右ペインの枠(border 1px + px-4 = 1.0625rem)はリングの right より内側なので差し引く -->
	<div
		class="flex items-center justify-between mb-1.5"
		style={reserveTopRight
			? 'padding-right: max(0px, calc(var(--idle-ring-reserve, 0px) - 1.0625rem))'
			: undefined}
	>
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
