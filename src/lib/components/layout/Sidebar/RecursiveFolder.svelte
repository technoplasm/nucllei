<script lang="ts" module>
	// サイドバーのフォルダ樹で使う最小型(Sidebar.svelte が API 応答から組み立てる)。
	export type SidebarFolderChat = {
		id: string;
		title: string;
	};

	export type SidebarFolder = {
		id: string;
		name: string;
		parent_id: string | null;
		is_expanded?: boolean;
		created_at: number;
		updated_at: number;
		childrenIds?: string[];
		items?: { chats?: SidebarFolderChat[] };
	};
</script>

<script lang="ts">
	import { getContext, tick } from 'svelte';

	const i18n = getContext('i18n');

	import DOMPurify from 'dompurify';
	import fileSaver from 'file-saver';
	const { saveAs } = fileSaver;

	import Collapsible from '../../common/Collapsible.svelte';
	import DragGhost from '$lib/components/common/DragGhost.svelte';

	import {
		deleteFolderById,
		updateFolderIsExpandedById,
		updateFolderNameById,
		updateFolderParentIdById
	} from '$lib/apis/folders';
	import { toast } from 'svelte-sonner';
	import { getChatById, getChatsByFolderId, importChat, updateChatFolderIdById } from '$lib/apis/chats';
	import RecursiveFolder from './RecursiveFolder.svelte';
	import ChatItem from './ChatItem.svelte';
	import FolderMenu from './Folders/FolderMenu.svelte';
	import DeleteConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';

	let {
		folders,
		folderId,
		className = '',
		parentDragged = false,
		onImport = undefined,
		onUpdate = undefined,
		onChange = undefined
	}: {
		folders: Record<string, SidebarFolder>;
		folderId: string;
		className?: string;
		parentDragged?: boolean;
		onImport?: (payload: { folderId: string; items: any[] }) => void;
		onUpdate?: () => void;
		onChange?: () => void;
	} = $props();

	// 初期値としてフォルダの永続化済み開閉状態を取り込む(以後はローカル状態)。
	// svelte-ignore state_referenced_locally
	let open = $state(folders[folderId]?.is_expanded ?? false);

	let folderElement: HTMLDivElement | null = $state(null);

	let edit = $state(false);

	let draggedOver = $state(false);
	let dragged = $state(false);

	let name = $state('');

	const onDragOver = (e: DragEvent) => {
		e.preventDefault();
		e.stopPropagation();
		if (dragged || parentDragged) {
			return;
		}
		draggedOver = true;
	};

	const onDrop = async (e: DragEvent) => {
		e.preventDefault();
		e.stopPropagation();
		if (dragged || parentDragged) {
			return;
		}

		if (folderElement && folderElement.contains(e.target as Node)) {
			if (e.dataTransfer?.items && e.dataTransfer.items.length > 0) {
				// Iterate over all items in the DataTransferItemList use functional programming
				for (const item of Array.from(e.dataTransfer.items)) {
					// If dropped items aren't files, reject them
					if (item.kind === 'file') {
						const file = item.getAsFile();
						if (file && file.type === 'application/json') {
							// Read the JSON file with FileReader
							const reader = new FileReader();
							reader.onload = async (event) => {
								try {
									const fileContent = JSON.parse(event.target?.result as string);
									open = true;
									onImport?.({
										folderId: folderId,
										items: fileContent
									});
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
						// Handle the drag-and-drop data for folders or chats (same as before)
						const dataTransfer = e.dataTransfer.getData('text/plain');
						const data = JSON.parse(dataTransfer);

						const { type, id, item } = data;

						if (type === 'folder') {
							open = true;
							if (id === folderId) {
								return;
							}
							// Move the folder
							const res = await updateFolderParentIdById(id, folderId).catch((error) => {
								toast.error(`${error}`);
								return null;
							});

							if (res) {
								onUpdate?.();
							}
						} else if (type === 'chat') {
							open = true;

							let chat = await getChatById(id).catch(() => {
								return null;
							});
							if (!chat && item) {
								chat = await importChat(item.chat, item?.meta ?? {});
							}
							if (!chat) {
								return;
							}

							// Move the chat
							const res = await updateChatFolderIdById(chat.id, folderId).catch((error) => {
								toast.error(`${error}`);
								return null;
							});

							if (res) {
								onUpdate?.();
							}
						}
					}
				}
			}

			draggedOver = false;
		}
	};

	const onDragLeave = (e: DragEvent) => {
		e.preventDefault();
		if (dragged || parentDragged) {
			return;
		}

		draggedOver = false;
	};

	const dragImage = new Image();
	dragImage.src =
		'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=';

	let x: number | undefined = $state();
	let y: number | undefined = $state();

	const onDragStart = (event: DragEvent) => {
		event.stopPropagation();
		event.dataTransfer?.setDragImage(dragImage, 0, 0);

		// Set the data to be transferred
		event.dataTransfer?.setData(
			'text/plain',
			JSON.stringify({
				type: 'folder',
				id: folderId
			})
		);

		dragged = true;
	};

	const onDrag = (event: DragEvent) => {
		event.stopPropagation();

		x = event.clientX;
		y = event.clientY;
	};

	const onDragEnd = (event: DragEvent) => {
		event.stopPropagation();
		dragged = false;
	};

	let showDeleteConfirm = $state(false);

	const deleteHandler = async () => {
		const res = await deleteFolderById(folderId).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			toast.success($i18n.t('Folder deleted successfully'));
			onUpdate?.();
		}
	};

	const nameUpdateHandler = async () => {
		if (name === '') {
			toast.error($i18n.t('Folder name cannot be empty'));
			return;
		}

		if (name === folders[folderId].name) {
			edit = false;
			return;
		}

		const currentName = folders[folderId].name;

		name = name.trim();
		folders[folderId].name = name;

		const res = await updateFolderNameById(folderId, name).catch((error) => {
			toast.error(`${error}`);

			folders[folderId].name = currentName;
			return null;
		});

		if (res) {
			folders[folderId].name = name;
			toast.success($i18n.t('Folder name updated successfully'));
			onUpdate?.();
		}
	};

	const isExpandedUpdateHandler = async () => {
		await updateFolderIsExpandedById(folderId, open).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
	};

	let isExpandedUpdateTimeout: ReturnType<typeof setTimeout>;

	// マウント時の初期値では発火させず、open が実際に変化したときだけ永続化する
	// (common/Switch.svelte の lastState ガードパターン)。
	// svelte-ignore state_referenced_locally
	let lastOpen = open;
	$effect(() => {
		if (open !== lastOpen) {
			lastOpen = open;
			clearTimeout(isExpandedUpdateTimeout);
			isExpandedUpdateTimeout = setTimeout(() => {
				isExpandedUpdateHandler();
			}, 500);
		}
	});

	const editHandler = async () => {
		await tick();
		name = folders[folderId].name;

		edit = true;
		await tick();

		const input = document.getElementById(`folder-${folderId}-input`);

		if (input) {
			input.focus();
		}
	};

	const exportHandler = async () => {
		const chats = await getChatsByFolderId(folderId).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		if (!chats) {
			return;
		}

		const blob = new Blob([JSON.stringify(chats)], {
			type: 'application/json'
		});

		saveAs(blob, `folder-${folders[folderId].name}-export-${Date.now()}.json`);
	};
</script>

<DeleteConfirmDialog
	bind:show={showDeleteConfirm}
	title={$i18n.t('Delete folder?')}
	onConfirm={() => {
		deleteHandler();
	}}
>
	<div class=" text-sm text-content flex-1 line-clamp-3">
		{@html DOMPurify.sanitize(
			$i18n.t('This will delete <strong>{{NAME}}</strong> and <strong>all its contents</strong>.', {
				NAME: folders[folderId].name
			})
		)}
	</div>
</DeleteConfirmDialog>

{#if dragged && x && y}
	<DragGhost {x} {y}>
		<div class=" bg-black/80 backdrop-blur-2xl px-2 py-1 rounded-lg w-fit max-w-40">
			<div class="flex items-center gap-1">
				<span class="i-lucide-folder-open size-3.5 block"></span>
				<div class=" text-xs text-white line-clamp-1">
					{folders[folderId].name}
				</div>
			</div>
		</div>
	</DragGhost>
{/if}

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
	bind:this={folderElement}
	class="relative {className}"
	style:opacity={dragged ? 0.5 : null}
	draggable="true"
	ondragover={onDragOver}
	ondrop={onDrop}
	ondragleave={onDragLeave}
	ondragstart={onDragStart}
	ondrag={onDrag}
	ondragend={onDragEnd}
>
	{#if draggedOver}
		<div
			class="absolute top-0 left-0 w-full h-full rounded-xs bg-gray-100/50 dark:bg-gray-700/20 bg-opacity-50 dark:bg-opacity-10 z-50 pointer-events-none touch-none"
		></div>
	{/if}

	<Collapsible
		bind:open
		className="w-full"
		buttonClassName="w-full"
		hide={(folders[folderId]?.childrenIds ?? []).length === 0 &&
			(folders[folderId].items?.chats ?? []).length === 0}
	>
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<div class="w-full group">
			<button
				id="folder-{folderId}-button"
				class="relative w-full py-1.5 px-2 rounded-md flex items-center gap-1.5 text-xs text-content-muted font-medium hover:bg-sidebar-hover"
				ondblclick={() => {
					editHandler();
				}}
			>
				<div class="text-content-subtle">
					{#if open}
						<span class="i-lucide-chevron-down size-3 block"></span>
					{:else}
						<span class="i-lucide-chevron-right size-3 block"></span>
					{/if}
				</div>

				<div class="translate-y-[0.5px] flex-1 justify-start text-start line-clamp-1">
					{#if edit}
						<input
							id="folder-{folderId}-input"
							type="text"
							bind:value={name}
							onfocus={(e) => {
								(e.target as HTMLInputElement).select();
							}}
							onblur={() => {
								nameUpdateHandler();
								edit = false;
							}}
							onclick={(e) => {
								// Prevent accidental collapse toggling when clicking inside input
								e.stopPropagation();
							}}
							onmousedown={(e) => {
								// Prevent accidental collapse toggling when clicking inside input
								e.stopPropagation();
							}}
							onkeydown={(e) => {
								if (e.key === 'Enter') {
									nameUpdateHandler();
									edit = false;
								}
							}}
							class="w-full h-full bg-transparent text-content-muted outline-hidden"
						/>
					{:else}
						{folders[folderId].name}
					{/if}
				</div>

				<div
					class="absolute z-10 right-2 invisible group-hover:visible self-center flex items-center text-content"
					onpointerup={(e) => {
						e.stopPropagation();
					}}
				>
					<FolderMenu
						onRename={() => {
							// Requires a timeout to prevent the click event from closing the dropdown
							setTimeout(() => {
								editHandler();
							}, 200);
						}}
						onDelete={() => {
							showDeleteConfirm = true;
						}}
						onExport={() => {
							exportHandler();
						}}
					>
						<div class="p-0.5 dark:hover:bg-gray-850 rounded-lg touch-auto">
							<span class="i-lucide-ellipsis size-4 block"></span>
						</div>
					</FolderMenu>
				</div>
			</button>
		</div>

		{#snippet content()}
			<div class="w-full">
				{#if (folders[folderId]?.childrenIds ?? []).length > 0 || (folders[folderId].items?.chats ?? []).length > 0}
					<div
						class="ml-3 pl-1 mt-[1px] flex flex-col overflow-y-auto scrollbar-hidden border-s border-gray-100 dark:border-gray-900"
					>
						{#if folders[folderId]?.childrenIds}
							{@const children = (folders[folderId]?.childrenIds ?? [])
								.map((id) => folders[id])
								.sort((a, b) =>
									a.name.localeCompare(b.name, undefined, {
										numeric: true,
										sensitivity: 'base'
									})
								)}

							{#each children as childFolder (`${folderId}-${childFolder.id}`)}
								<RecursiveFolder
									{folders}
									folderId={childFolder.id}
									parentDragged={dragged}
									{onImport}
									{onUpdate}
									{onChange}
								/>
							{/each}
						{/if}

						{#if folders[folderId].items?.chats}
							{#each folders[folderId].items.chats as chat (chat.id)}
								<ChatItem id={chat.id} title={chat.title} {onChange} />
							{/each}
						{/if}
					</div>
				{/if}
			</div>
		{/snippet}
	</Collapsible>
</div>
