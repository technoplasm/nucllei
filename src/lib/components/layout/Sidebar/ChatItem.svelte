<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';
	import { getContext, tick } from 'svelte';
	const i18n = getContext('i18n');

	import {
		archiveChatById,
		cloneChatById,
		deleteChatById,
		getChatById,
		getChatList,
		getPinnedChatList,
		updateChatById
	} from '$lib/apis/chats';
	import {
		chatId,
		chatTitle as _chatTitle,
		chats,
		mobile,
		pinnedChats,
		showSidebar,
		currentChatPage
	} from '$lib/stores';

	import { chatChildrenState } from '$lib/stores/chat-children.svelte';

	import ChatMenu from './ChatMenu.svelte';
	import DeleteConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import ShareChatModal from '$lib/components/chat/ShareChatModal.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import DragGhost from '$lib/components/common/DragGhost.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	let {
		className = '',
		id,
		title,
		selected = false,
		shiftKey = false,
		onSelect = undefined,
		onUnselect = undefined,
		onChange = undefined
	}: {
		className?: string;
		id: string;
		title: string;
		selected?: boolean;
		shiftKey?: boolean;
		onSelect?: () => void;
		onUnselect?: () => void;
		onChange?: () => void;
	} = $props();

	let chat: Record<string, any> | null = $state(null);

	let mouseOver = $state(false);
	let draggable = $state(false);
	$effect(() => {
		if (mouseOver) {
			loadChat();
		}
	});

	const loadChat = async () => {
		if (!chat) {
			draggable = false;
			chat = await getChatById(id);
			draggable = true;
		}
	};

	let showShareChatModal = $state(false);
	let confirmEdit = $state(false);

	// 編集開始時に必ず title を代入するので初期値は空でよい(state_referenced_locally 回避)
	let chatTitle = $state('');

	const editChatTitle = async (id: string, title: string) => {
		if (title === '') {
			toast.error($i18n.t('Title cannot be an empty string.'));
		} else {
			await updateChatById(id, {
				title: title
			});

			if (id === $chatId) {
				_chatTitle.set(title);
			}

			currentChatPage.set(1);
			await chats.set(await getChatList($currentChatPage));
			await pinnedChats.set(await getPinnedChatList());

			onChange?.();
		}
	};

	const cloneChatHandler = async (id: string) => {
		const res = await cloneChatById(
			id,
			$i18n.t('Clone of {{TITLE}}', {
				TITLE: title
			})
		).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			goto(`/c/${res.id}`);

			currentChatPage.set(1);
			await chats.set(await getChatList($currentChatPage));
			await pinnedChats.set(await getPinnedChatList());
		}
	};

	const deleteChatHandler = async (id: string) => {
		const res = await deleteChatById(id).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			if ($chatId === id) {
				await goto('/');

				await chatId.set('');
				await tick();
			}

			onChange?.();
		}
	};

	// archive API はトグル(archived を反転)— 連打すると往復して戻ってしまうので、
	// 応答が返るまで次の押下を受けない。表示中のチャットをアーカイブしたら開始画面へ
	let archiving = false;
	const archiveChatHandler = async (id: string) => {
		if (archiving) {
			return;
		}
		archiving = true;
		try {
			const res = await archiveChatById(id).catch((error) => {
				toast.error(`${error?.detail ?? error}`);
				return null;
			});
			if (!res) {
				return;
			}
			if (res.archived && $chatId === id) {
				await goto('/');

				await chatId.set('');
				await tick();
			}
			onChange?.();
		} finally {
			archiving = false;
		}
	};

	const focusEdit = (node: HTMLInputElement) => {
		node.focus();
	};

	let itemElement: HTMLDivElement | undefined = $state();

	let dragged = $state(false);
	let x = $state(0);
	let y = $state(0);

	const dragImage = new Image();
	dragImage.src =
		'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=';

	const onDragStart = (event: DragEvent) => {
		event.stopPropagation();

		event.dataTransfer?.setDragImage(dragImage, 0, 0);

		// Set the data to be transferred
		event.dataTransfer?.setData(
			'text/plain',
			JSON.stringify({
				type: 'chat',
				id: id,
				item: chat
			})
		);

		dragged = true;
		if (itemElement) {
			itemElement.style.opacity = '0.5'; // Optional: Visual cue to show it's being dragged
		}
	};

	const onDrag = (event: DragEvent) => {
		event.stopPropagation();

		x = event.clientX;
		y = event.clientY;
	};

	const onDragEnd = (event: DragEvent) => {
		event.stopPropagation();

		if (itemElement) {
			itemElement.style.opacity = '1'; // Reset visual cue after drag
		}
		dragged = false;
	};

	let showDeleteConfirm = $state(false);

	const chatTitleInputKeydownHandler = (e: KeyboardEvent) => {
		if (e.key === 'Enter') {
			e.preventDefault();
			editChatTitle(id, chatTitle);
			confirmEdit = false;
			chatTitle = '';
		} else if (e.key === 'Escape') {
			e.preventDefault();
			confirmEdit = false;
			chatTitle = '';
		}
	};
</script>

<ShareChatModal bind:show={showShareChatModal} chatId={id} />

<DeleteConfirmDialog
	bind:show={showDeleteConfirm}
	title={$i18n.t('Delete chat?')}
	onConfirm={() => {
		deleteChatHandler(id);
	}}
>
	<div class=" text-sm text-content-muted flex-1 line-clamp-3">
		{$i18n.t('This will delete')} <span class="  font-semibold">{title}</span>.
	</div>
</DeleteConfirmDialog>

{#if dragged && x && y}
	<DragGhost {x} {y}>
		<div class=" bg-black/80 backdrop-blur-2xl px-2 py-1 rounded-lg w-fit max-w-40">
			<div class="flex items-center gap-1">
				<span class="i-lucide-file size-[18px] block"></span>
				<div class=" text-xs text-white line-clamp-1">
					{title}
				</div>
			</div>
		</div>
	</DragGhost>
{/if}

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
	bind:this={itemElement}
	class=" w-full {className} relative group"
	draggable={draggable && !confirmEdit}
	ondragstart={onDragStart}
	ondrag={onDrag}
	ondragend={onDragEnd}
>
	{#if confirmEdit}
		<div
			class=" w-full flex justify-between rounded-lg px-2 py-1.5 {id === $chatId || confirmEdit
				? 'bg-sidebar-selected'
				: selected
					? 'bg-sidebar-selected'
					: 'group-hover:bg-sidebar-hover'}  whitespace-nowrap text-ellipsis"
		>
			<input
				use:focusEdit
				bind:value={chatTitle}
				id="chat-title-input-{id}"
				class=" bg-transparent w-full outline-hidden mr-10"
				onkeydown={chatTitleInputKeydownHandler}
			/>
		</div>
	{:else}
		<a
			class=" w-full flex justify-between rounded-lg px-2 py-1.5 {id === $chatId || confirmEdit
				? 'bg-sidebar-selected'
				: selected
					? 'bg-sidebar-selected'
					: ' group-hover:bg-sidebar-hover'}  whitespace-nowrap text-ellipsis"
			href="/c/{id}"
			onclick={() => {
				onSelect?.();

				if ($mobile) {
					showSidebar.set(false);
				}
			}}
			ondblclick={() => {
				chatTitle = title;
				confirmEdit = true;
			}}
			onmouseenter={() => {
				mouseOver = true;
			}}
			onmouseleave={() => {
				mouseOver = false;
			}}
			draggable="false"
		>
			<div class=" flex self-center flex-1 w-full">
				<div dir="auto" class="text-left self-center overflow-hidden w-full h-[20px]">
					{title}
				</div>
			</div>
		</a>
	{/if}

	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="
			absolute top-[4px] py-1 pr-0.5 mr-1.5 pl-5 bg-linear-to-l from-80% to-transparent
			{id === $chatId || confirmEdit
			? 'from-sidebar-selected'
			: selected
				? 'from-sidebar-selected'
				: 'invisible group-hover:visible from-sidebar-hover'}
			{className === 'pr-2' ? 'right-[8px]' : 'right-0'}"
		onmouseenter={() => {
			mouseOver = true;
		}}
		onmouseleave={() => {
			mouseOver = false;
		}}
	>
		{#if confirmEdit}
			<div
				class="flex self-center items-center space-x-1.5 z-10 translate-y-[0.5px] -translate-x-[0.5px]"
			>
				<Tooltip content={$i18n.t('Confirm')}>
					<button
						class=" self-center dark:hover:text-white"
						aria-label={$i18n.t('Confirm')}
						onclick={() => {
							editChatTitle(id, chatTitle);
							confirmEdit = false;
							chatTitle = '';
						}}
					>
						<span class="i-lucide-check size-3.5 block"></span>
					</button>
				</Tooltip>

				<Tooltip content={$i18n.t('Cancel')}>
					<button
						class=" self-center dark:hover:text-white"
						aria-label={$i18n.t('Cancel')}
						onclick={() => {
							confirmEdit = false;
							chatTitle = '';
						}}
					>
						<span class="i-lucide-x size-4 block"></span>
					</button>
				</Tooltip>
			</div>
		{:else if shiftKey && mouseOver}
			<div class=" flex items-center self-center space-x-1.5">
				<Tooltip content={$i18n.t('Archive')} className="flex items-center">
					<button
						class=" self-center dark:hover:text-white"
						aria-label={$i18n.t('Archive')}
						onclick={() => {
							archiveChatHandler(id);
						}}
						type="button"
					>
						<span class="i-lucide-archive size-4 block translate-y-[0.5px]"></span>
					</button>
				</Tooltip>

				<Tooltip content={$i18n.t('Delete')}>
					<button
						class=" self-center dark:hover:text-white"
						aria-label={$i18n.t('Delete')}
						onclick={() => {
							deleteChatHandler(id);
						}}
						type="button"
					>
						<span class="i-lucide-trash-2 size-4 block"></span>
					</button>
				</Tooltip>
			</div>
		{:else}
			<div class="flex self-center gap-1 z-10">
				<ChatMenu
					chatId={id}
					cloneChatHandler={() => {
						cloneChatHandler(id);
					}}
					shareHandler={() => {
						showShareChatModal = true;
					}}
					archiveChatHandler={() => {
						archiveChatHandler(id);
					}}
					renameHandler={async () => {
						chatTitle = title;
						confirmEdit = true;

						await tick();
						const input = document.getElementById(`chat-title-input-${id}`);
						if (input) {
							input.focus();
						}
					}}
					deleteHandler={() => {
						showDeleteConfirm = true;
					}}
					onClose={() => {
						onUnselect?.();
					}}
					onChange={() => {
						onChange?.();
					}}
				>
					<button
						aria-label="Chat Menu"
						class=" self-center dark:hover:text-white"
						onclick={() => {
							onSelect?.();
						}}
					>
						<span class="i-lucide-ellipsis size-4 block"></span>
					</button>
				</ChatMenu>

				{#if id === $chatId}
					<!-- Shortcut support using "delete-chat-button" id -->
					<button
						id="delete-chat-button"
						class="hidden"
						aria-label={$i18n.t('Delete Chat')}
						onclick={() => {
							showDeleteConfirm = true;
						}}
					>
						<span class="i-lucide-trash-2 size-4 block"></span>
					</button>
				{/if}
			</div>
		{/if}
	</div>
</div>

<!-- llens(#107 P3): 選択中 chat の子 agent session tree。選択中(または観測中の子の親)
     の項目の下にだけ出す(chatChildrenState は 1 親分しか持たない)。 -->
{#if chatChildrenState.parentId === id && chatChildrenState.entries.length > 0}
	<div class="ml-4 pl-1 flex flex-col border-s border-gray-100 dark:border-gray-900">
		{#each chatChildrenState.entries as child (child.id)}
			<a
				class="w-full flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs {child.id === $chatId
					? 'bg-sidebar-selected'
					: 'hover:bg-sidebar-hover'}"
				href="/c/{child.id}"
				draggable="false"
				onclick={() => {
					if ($mobile) {
						showSidebar.set(false);
					}
				}}
			>
				<span class="i-lucide-corner-down-right size-3 block shrink-0 text-content-muted"></span>
				<div dir="auto" class="flex-1 text-left truncate" title={child.title}>
					{child.title}
				</div>
				{#if child.running}
					<Spinner className="size-3 shrink-0" />
				{/if}
			</a>
		{/each}
	</div>
{/if}
