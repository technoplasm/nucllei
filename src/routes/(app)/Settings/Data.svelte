<script lang="ts">
	import fileSaver from 'file-saver';
	const { saveAs } = fileSaver;

	import { chats, scrollPaginationEnabled, currentChatPage } from '$lib/stores';

	import {
		archiveAllChats,
		createNewChat,
		deleteAllChats,
		getAllChats,
		getChatList
	} from '$lib/apis/chats';
	import { getImportOrigin, convertOpenAIChats } from '$lib/utils';
	import { getContext } from 'svelte';
	import { goto } from '$app/navigation';
	import { toast } from 'svelte-sonner';
	import ArchivedChatsModal from '$lib/components/layout/Sidebar/ArchivedChatsModal.svelte';

	const i18n = getContext('i18n');

	let importFiles = $state<FileList | null>(null);

	let showArchiveConfirm = $state(false);
	let showDeleteConfirm = $state(false);
	let showArchivedChatsModal = $state(false);

	let chatImportInputElement = $state<HTMLInputElement | null>(null);

	const importChats = async (_chats: any[]) => {
		for (const chat of _chats) {
			if (chat.chat) {
				await createNewChat(chat.chat);
			} else {
				await createNewChat(chat);
			}
		}

		currentChatPage.set(1);
		await chats.set(await getChatList($currentChatPage));
		scrollPaginationEnabled.set(true);
	};

	const importChatsInputHandler = () => {
		if (!importFiles || importFiles.length === 0) {
			return;
		}

		let reader = new FileReader();
		reader.onload = (event) => {
			let chats = JSON.parse(event.target?.result as string);
			if (getImportOrigin(chats) == 'openai') {
				try {
					chats = convertOpenAIChats(chats);
				} catch {
					toast.error($i18n.t('Unable to import chats'));
					return;
				}
			}
			importChats(chats);
		};

		reader.readAsText(importFiles[0]);
	};

	const exportChats = async () => {
		let blob = new Blob([JSON.stringify(await getAllChats())], {
			type: 'application/json'
		});
		saveAs(blob, `chat-export-${Date.now()}.json`);
	};

	const archiveAllChatsHandler = async () => {
		await goto('/');
		await archiveAllChats().catch((error) => {
			toast.error(`${error}`);
		});

		currentChatPage.set(1);
		await chats.set(await getChatList($currentChatPage));
		scrollPaginationEnabled.set(true);
	};

	const deleteAllChatsHandler = async () => {
		await goto('/');
		await deleteAllChats().catch((error) => {
			toast.error(`${error}`);
		});

		currentChatPage.set(1);
		await chats.set(await getChatList($currentChatPage));
		scrollPaginationEnabled.set(true);
	};

	const handleArchivedChatsChange = async () => {
		currentChatPage.set(1);
		await chats.set(await getChatList($currentChatPage));
		scrollPaginationEnabled.set(true);
	};
</script>

<ArchivedChatsModal bind:show={showArchivedChatsModal} onChange={handleArchivedChatsChange} />

<div class="flex flex-col h-full justify-between space-y-3 text-sm">
	<div class=" space-y-2 flex-1 min-h-0 overflow-y-auto">
		<div class="flex flex-col">
			<div class=" mb-1.5 text-md font-medium">{$i18n.t('Data')}</div>

			<input
				id="chat-import-input"
				bind:this={chatImportInputElement}
				bind:files={importFiles}
				onchange={importChatsInputHandler}
				type="file"
				accept=".json"
				hidden
			/>
			<button
				class=" flex rounded-md py-2 px-2 w-full hover:bg-surface-hover"
				type="button"
				onclick={() => {
					chatImportInputElement?.click();
				}}
			>
				<div class=" self-center mr-3">
					<span class="i-lucide-file-up size-3 block"></span>
				</div>
				<div class=" self-center text-xs font-medium">{$i18n.t('Import Chats')}</div>
			</button>
			<button
				class=" flex rounded-md py-2 px-2 w-full hover:bg-surface-hover"
				type="button"
				onclick={() => {
					exportChats();
				}}
			>
				<div class=" self-center mr-3">
					<span class="i-lucide-file-down size-3 block"></span>
				</div>
				<div class=" self-center text-xs font-medium">{$i18n.t('Export Chats')}</div>
			</button>
		</div>

		<hr class=" border-line" />

		<div class="flex flex-col">
			<button
				class=" flex rounded-md py-1 px-2 w-full hover:bg-surface-hover"
				type="button"
				onclick={() => {
					showArchivedChatsModal = true;
				}}
			>
				<div class=" self-center mr-3">
					<span class="i-lucide-archive size-3 block"></span>
				</div>
				<div class=" self-center text-xs font-medium">{$i18n.t('Archived Chats')}</div>
			</button>

			{#if showArchiveConfirm}
				<div class="flex justify-between rounded-md items-center py-2 px-3.5 w-full">
					<div class="flex items-center space-x-3">
						<span class="i-lucide-archive size-4 block"></span>
						<span>{$i18n.t('Are you sure?')}</span>
					</div>

					<div class="flex space-x-1.5 items-center">
						<button
							class="hover:text-content"
							type="button"
							aria-label={$i18n.t('Confirm')}
							onclick={() => {
								archiveAllChatsHandler();
								showArchiveConfirm = false;
							}}
						>
							<span class="i-lucide-check size-4 block"></span>
						</button>
						<button
							class="hover:text-content"
							type="button"
							aria-label={$i18n.t('Cancel')}
							onclick={() => {
								showArchiveConfirm = false;
							}}
						>
							<span class="i-lucide-x size-4 block"></span>
						</button>
					</div>
				</div>
			{:else}
				<button
					class=" flex rounded-md py-2 px-3.5 w-full hover:bg-surface-hover"
					type="button"
					onclick={() => {
						showArchiveConfirm = true;
					}}
				>
					<div class=" self-center mr-3">
						<span class="i-lucide-archive size-3 block"></span>
					</div>
					<div class=" self-center text-xs font-medium">{$i18n.t('Archive All Chats')}</div>
				</button>
			{/if}

			{#if showDeleteConfirm}
				<div class="flex justify-between rounded-md items-center py-2 px-3.5 w-full">
					<div class="flex items-center space-x-3">
						<span class="i-lucide-trash-2 size-4 block"></span>
						<span>{$i18n.t('Are you sure?')}</span>
					</div>

					<div class="flex space-x-1.5 items-center">
						<button
							class="hover:text-content"
							type="button"
							aria-label={$i18n.t('Confirm')}
							onclick={() => {
								deleteAllChatsHandler();
								showDeleteConfirm = false;
							}}
						>
							<span class="i-lucide-check size-4 block"></span>
						</button>
						<button
							class="hover:text-content"
							type="button"
							aria-label={$i18n.t('Cancel')}
							onclick={() => {
								showDeleteConfirm = false;
							}}
						>
							<span class="i-lucide-x size-4 block"></span>
						</button>
					</div>
				</div>
			{:else}
				<button
					class=" flex rounded-md py-2 px-3.5 w-full hover:bg-surface-hover"
					type="button"
					onclick={() => {
						showDeleteConfirm = true;
					}}
				>
					<div class=" self-center mr-3">
						<span class="i-lucide-trash-2 size-3 block"></span>
					</div>
					<div class=" self-center text-xs font-medium">{$i18n.t('Delete All Chats')}</div>
				</button>
			{/if}
		</div>
	</div>
</div>
