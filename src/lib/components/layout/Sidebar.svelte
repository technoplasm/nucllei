<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { DEFAULT_MODEL_IMAGE_URL } from '$lib/constants';
	import { v4 as uuidv4 } from 'uuid';

	import { goto } from '$app/navigation';
	import {
		WEBUI_NAME,
		user,
		chats,
		models,
		settings,
		showSettings,
		chatId,
		showSidebar,
		mobile,
		showArchivedChats,
		pinnedChats,
		scrollPaginationEnabled,
		currentChatPage,
		socket,
		config,
		isApp
	} from '$lib/stores';
	import { onMount, getContext, tick, onDestroy } from 'svelte';

	const i18n = getContext('i18n');

	import {
		deleteChatById,
		getChatList,
		getChatListBySearchText,
		createNewChat,
		getPinnedChatList,
		toggleChatPinnedStatusById,
		getChatPinnedStatusById,
		getChatById,
		updateChatFolderIdById,
		importChat
	} from '$lib/apis/chats';
	import { createNewFolder, getFolders, updateFolderParentIdById } from '$lib/apis/folders';

	import ArchivedChatsModal from './Sidebar/ArchivedChatsModal.svelte';
	import UserMenu from './Sidebar/UserMenu.svelte';
	import ChatItem from './Sidebar/ChatItem.svelte';
	import Spinner from '../common/Spinner.svelte';
	import Loader from '../common/Loader.svelte';
	import AddFilesPlaceholder from '../AddFilesPlaceholder.svelte';
	import Folder from '../common/Folder.svelte';
	import Plus from '../icons/Plus.svelte';
	import Tooltip from '../common/Tooltip.svelte';
	import Folders from './Sidebar/Folders.svelte';
	import PencilSquare from '../icons/PencilSquare.svelte';
	import Pencil from '../icons/Pencil.svelte';
	import Home from '../icons/Home.svelte';
	import Cog6 from '../icons/Cog6.svelte';

	const BREAKPOINT = 768;

	let navElement;
	let search = '';

	// サイドバー「モデル」欄: 有効(非 hidden)なモデルのみ。$models と連動して自動反映。
	$: enabledModels = ($models ?? []).filter(
		(m) => m?.id && (m?.info?.meta?.hidden ?? false) !== true
	);

	// モデル行クリック: そのモデルを選択した状態で新規チャットを開く(?models= を initNewChat が解釈)。
	const modelChatHandler = async (modelId) => {
		selectedChatId = null;
		await goto(`/?models=${encodeURIComponent(modelId)}`);
		const newChatButton = document.getElementById('new-chat-button');
		setTimeout(() => {
			newChatButton?.click();
			if ($mobile) {
				showSidebar.set(false);
			}
		}, 0);
	};

	let shiftKey = false;

	let selectedChatId = null;
	let showDropdown = false;
	let showPinnedChat = true;

	// Pagination variables
	let chatListLoading = false;
	let allChatsLoaded = false;

	let folders = {};
	let newFolderId = null;

	const initFolders = async () => {
		const folderList = await getFolders(localStorage.token).catch((error) => {
			toast.error(`${error}`);
			return [];
		});

		folders = {};

		// First pass: Initialize all folder entries
		for (const folder of folderList) {
			// Ensure folder is added to folders with its data
			folders[folder.id] = { ...(folders[folder.id] || {}), ...folder };

			if (newFolderId && folder.id === newFolderId) {
				folders[folder.id].new = true;
				newFolderId = null;
			}
		}

		// Second pass: Tie child folders to their parents
		for (const folder of folderList) {
			if (folder.parent_id) {
				// Ensure the parent folder is initialized if it doesn't exist
				if (!folders[folder.parent_id]) {
					folders[folder.parent_id] = {}; // Create a placeholder if not already present
				}

				// Initialize childrenIds array if it doesn't exist and add the current folder id
				folders[folder.parent_id].childrenIds = folders[folder.parent_id].childrenIds
					? [...folders[folder.parent_id].childrenIds, folder.id]
					: [folder.id];

				// Sort the children by updated_at field
				folders[folder.parent_id].childrenIds.sort((a, b) => {
					return folders[b].updated_at - folders[a].updated_at;
				});
			}
		}
	};

	const createFolder = async (name = 'Untitled') => {
		if (name === '') {
			toast.error($i18n.t('Folder name cannot be empty.'));
			return;
		}

		const rootFolders = Object.values(folders).filter((folder) => folder.parent_id === null);
		if (rootFolders.find((folder) => folder.name.toLowerCase() === name.toLowerCase())) {
			// If a folder with the same name already exists, append a number to the name
			let i = 1;
			while (
				rootFolders.find((folder) => folder.name.toLowerCase() === `${name} ${i}`.toLowerCase())
			) {
				i++;
			}

			name = `${name} ${i}`;
		}

		// Add a dummy folder to the list to show the user that the folder is being created
		const tempId = uuidv4();
		folders = {
			...folders,
			tempId: {
				id: tempId,
				name: name,
				created_at: Date.now(),
				updated_at: Date.now()
			}
		};

		const res = await createNewFolder(localStorage.token, name).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			newFolderId = res.id;
			await initFolders();
		}
	};

	const initChatList = async () => {
		// Reset pagination variables
		pinnedChats.set(await getPinnedChatList(localStorage.token));
		initFolders();

		currentChatPage.set(1);
		allChatsLoaded = false;

		if (search) {
			await chats.set(await getChatListBySearchText(localStorage.token, search, $currentChatPage));
		} else {
			await chats.set(await getChatList(localStorage.token, $currentChatPage));
		}

		// Enable pagination
		scrollPaginationEnabled.set(true);
	};

	const loadMoreChats = async () => {
		chatListLoading = true;

		currentChatPage.set($currentChatPage + 1);

		let newChatList = [];

		if (search) {
			newChatList = await getChatListBySearchText(localStorage.token, search, $currentChatPage);
		} else {
			newChatList = await getChatList(localStorage.token, $currentChatPage);
		}

		// once the bottom of the list has been reached (no results) there is no need to continue querying
		allChatsLoaded = newChatList.length === 0;
		await chats.set([...($chats ? $chats : []), ...newChatList]);

		chatListLoading = false;
	};

	const importChatHandler = async (items, pinned = false, folderId = null) => {
		console.log('importChatHandler', items, pinned, folderId);
		for (const item of items) {
			console.log(item);
			if (item.chat) {
				await importChat(localStorage.token, item.chat, item?.meta ?? {}, pinned, folderId);
			}
		}

		initChatList();
	};

	const inputFilesHandler = async (files) => {
		console.log(files);

		for (const file of files) {
			const reader = new FileReader();
			reader.onload = async (e) => {
				const content = e.target.result;

				try {
					const chatItems = JSON.parse(content);
					importChatHandler(chatItems);
				} catch {
					toast.error($i18n.t(`Invalid file format.`));
				}
			};

			reader.readAsText(file);
		}
	};

	let draggedOver = false;

	const onDragOver = (e) => {
		e.preventDefault();

		// Check if a file is being draggedOver.
		if (e.dataTransfer?.types?.includes('Files')) {
			draggedOver = true;
		} else {
			draggedOver = false;
		}
	};

	const onDragLeave = () => {
		draggedOver = false;
	};

	const onDrop = async (e) => {
		e.preventDefault();
		console.log(e); // Log the drop event

		// Perform file drop check and handle it accordingly
		if (e.dataTransfer?.files) {
			const inputFiles = Array.from(e.dataTransfer?.files);

			if (inputFiles && inputFiles.length > 0) {
				console.log(inputFiles); // Log the dropped files
				inputFilesHandler(inputFiles); // Handle the dropped files
			}
		}

		draggedOver = false; // Reset draggedOver status after drop
	};

	let touchstart;
	let touchend;

	function checkDirection() {
		const screenWidth = window.innerWidth;
		const swipeDistance = Math.abs(touchend.screenX - touchstart.screenX);
		if (touchstart.clientX < 40 && swipeDistance >= screenWidth / 8) {
			if (touchend.screenX < touchstart.screenX) {
				showSidebar.set(false);
			}
			if (touchend.screenX > touchstart.screenX) {
				showSidebar.set(true);
			}
		}
	}

	const onTouchStart = (e) => {
		touchstart = e.changedTouches[0];
		console.log(touchstart.clientX);
	};

	const onTouchEnd = (e) => {
		touchend = e.changedTouches[0];
		checkDirection();
	};

	const onKeyDown = (e) => {
		if (e.key === 'Shift') {
			shiftKey = true;
		}
	};

	const onKeyUp = (e) => {
		if (e.key === 'Shift') {
			shiftKey = false;
		}
	};

	const onFocus = () => {};

	const onBlur = () => {
		shiftKey = false;
		selectedChatId = null;
	};

	onMount(async () => {
		showPinnedChat = localStorage?.showPinnedChat ? localStorage.showPinnedChat === 'true' : true;

		mobile.subscribe((value) => {
			if ($showSidebar && value) {
				showSidebar.set(false);
			}

			if ($showSidebar && !value) {
				const navElement = document.getElementsByTagName('nav')[0];
				if (navElement) {
					navElement.style['-webkit-app-region'] = 'drag';
				}
			}

			if (!$showSidebar && !value) {
				showSidebar.set(true);
			}
		});

		showSidebar.set(!$mobile ? localStorage.sidebar === 'true' : false);
		showSidebar.subscribe((value) => {
			localStorage.sidebar = value;

			// nav element is not available on the first render
			const navElement = document.getElementsByTagName('nav')[0];

			if (navElement) {
				if ($mobile) {
					if (!value) {
						navElement.style['-webkit-app-region'] = 'drag';
					} else {
						navElement.style['-webkit-app-region'] = 'no-drag';
					}
				} else {
					navElement.style['-webkit-app-region'] = 'drag';
				}
			}
		});

		await initChatList();

		window.addEventListener('keydown', onKeyDown);
		window.addEventListener('keyup', onKeyUp);

		window.addEventListener('touchstart', onTouchStart);
		window.addEventListener('touchend', onTouchEnd);

		window.addEventListener('focus', onFocus);
		window.addEventListener('blur-sm', onBlur);

		const dropZone = document.getElementById('sidebar');

		dropZone?.addEventListener('dragover', onDragOver);
		dropZone?.addEventListener('drop', onDrop);
		dropZone?.addEventListener('dragleave', onDragLeave);
	});

	onDestroy(() => {
		window.removeEventListener('keydown', onKeyDown);
		window.removeEventListener('keyup', onKeyUp);

		window.removeEventListener('touchstart', onTouchStart);
		window.removeEventListener('touchend', onTouchEnd);

		window.removeEventListener('focus', onFocus);
		window.removeEventListener('blur-sm', onBlur);

		const dropZone = document.getElementById('sidebar');

		dropZone?.removeEventListener('dragover', onDragOver);
		dropZone?.removeEventListener('drop', onDrop);
		dropZone?.removeEventListener('dragleave', onDragLeave);
	});
</script>

<ArchivedChatsModal
	bind:show={$showArchivedChats}
	on:change={async () => {
		await initChatList();
	}}
/>

<!-- svelte-ignore a11y-no-static-element-interactions -->

{#if $showSidebar}
	<div
		class=" {$isApp
			? ' ml-[4.5rem] md:ml-0'
			: ''} fixed md:hidden z-40 top-0 right-0 left-0 bottom-0 bg-black/60 w-full min-h-screen h-screen flex justify-center overflow-hidden overscroll-contain"
		on:mousedown={() => {
			showSidebar.set(!$showSidebar);
		}}
	/>
{/if}

<div
	bind:this={navElement}
	id="sidebar"
	class="h-screen max-h-[100dvh] min-h-screen select-none {$showSidebar
		? 'md:relative w-[300px] max-w-[300px]'
		: '-translate-x-[300px] w-[0px]'} {$isApp
		? `ml-[4.5rem] md:ml-0 `
		: 'transition-width duration-200 ease-in-out'}  shrink-0 bg-surface-sidebar text-gray-900 dark:text-gray-200 fixed z-50 top-0 left-0 overflow-x-hidden"
	data-state={$showSidebar}
>
	<div
		class="py-1 my-auto flex flex-col justify-between h-screen max-h-[100dvh] w-[300px] overflow-x-hidden z-50 {$showSidebar
			? ''
			: 'invisible'}"
	>
		<div
			class="px-1.5 flex justify-between items-center space-x-1 text-gray-600 dark:text-gray-400"
		>
			<a
				class="px-1 py-2 flex items-center gap-1.5 min-w-0 flex-1 rounded-lg no-drag-region"
				href="/"
				draggable="false"
				on:click={() => {
					if ($mobile) {
						showSidebar.set(false);
					}
				}}
			>
				<img
					crossorigin="anonymous"
					src="/static/favicon.png"
					class="size-6 rounded-full shrink-0"
					alt="logo"
				/>
				<div
					class="self-center font-medium text-md text-gray-850 dark:text-white font-primary truncate"
				>
					{$WEBUI_NAME}
				</div>
			</a>

			<button
				class=" cursor-pointer p-[7px] flex rounded-xl hover:bg-sidebar-hover transition shrink-0"
				on:click={() => {
					showSidebar.set(!$showSidebar);
				}}
				aria-label="Toggle Sidebar"
			>
				<div class=" m-auto self-center">
					<svg
						xmlns="http://www.w3.org/2000/svg"
						fill="none"
						viewBox="0 0 24 24"
						stroke-width="2"
						stroke="currentColor"
						class="size-5"
					>
						<path
							stroke-linecap="round"
							stroke-linejoin="round"
							d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25H12"
						/>
					</svg>
				</div>
			</button>
		</div>

		<div class="px-1.5 flex justify-center text-gray-800 dark:text-gray-200 mt-0.5">
			<a
				id="sidebar-new-chat-button"
				class=" px-2 py-1.5 grow flex items-center space-x-3 rounded-lg hover:bg-sidebar-hover transition no-drag-region"
				href="/"
				draggable="false"
				on:click={async () => {
					selectedChatId = null;
					await goto('/');
					const newChatButton = document.getElementById('new-chat-button');
					setTimeout(() => {
						newChatButton?.click();
						if ($mobile) {
							showSidebar.set(false);
						}
					}, 0);
				}}
			>
				<div class="self-center">
					<PencilSquare className="size-[1.1rem]" strokeWidth="2" />
				</div>
				<div class="flex self-center translate-y-[0.5px]">
					<div class=" self-center font-medium text-sm font-primary">{$i18n.t('New Chat')}</div>
				</div>
			</a>
		</div>

		{#if $user?.role === 'admin' || $user?.permissions?.workspace?.models || $user?.permissions?.workspace?.skills || $user?.permissions?.workspace?.tools}
			<div class="px-1.5 flex justify-center text-gray-800 dark:text-gray-200">
				<a
					class="px-2 py-1.5 grow flex items-center space-x-3 rounded-lg hover:bg-sidebar-hover transition"
					href="/workspace"
					on:click={() => {
						selectedChatId = null;
						chatId.set('');

						if ($mobile) {
							showSidebar.set(false);
						}
					}}
					draggable="false"
				>
					<div class="self-center">
						<svg
							xmlns="http://www.w3.org/2000/svg"
							fill="none"
							viewBox="0 0 24 24"
							stroke-width="2"
							stroke="currentColor"
							class="size-[1.1rem]"
						>
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								d="M13.5 16.875h3.375m0 0h3.375m-3.375 0V13.5m0 3.375v3.375M6 10.5h2.25a2.25 2.25 0 0 0 2.25-2.25V6a2.25 2.25 0 0 0-2.25-2.25H6A2.25 2.25 0 0 0 3.75 6v2.25A2.25 2.25 0 0 0 6 10.5Zm0 9.75h2.25A2.25 2.25 0 0 0 10.5 18v-2.25a2.25 2.25 0 0 0-2.25-2.25H6a2.25 2.25 0 0 0-2.25 2.25V18A2.25 2.25 0 0 0 6 20.25Zm9.75-9.75H18a2.25 2.25 0 0 0 2.25-2.25V6A2.25 2.25 0 0 0 18 3.75h-2.25A2.25 2.25 0 0 0 13.5 6v2.25a2.25 2.25 0 0 0 2.25 2.25Z"
							/>
						</svg>
					</div>

					<div class="flex self-center translate-y-[0.5px]">
						<div class=" self-center font-medium text-sm font-primary">{$i18n.t('Workspace')}</div>
					</div>
				</a>
			</div>
		{/if}

		<div class="px-1.5 flex justify-center text-gray-800 dark:text-gray-200">
			<button
				class="px-2 py-1.5 grow flex items-center space-x-3 rounded-lg hover:bg-sidebar-hover transition"
				on:click={async () => {
					await showSettings.set(true);
					if ($mobile) {
						showSidebar.set(false);
					}
				}}
			>
				<div class="self-center">
					<Cog6 className="size-[1.1rem]" strokeWidth="2" />
				</div>
				<div class="flex self-center translate-y-[0.5px]">
					<div class=" self-center font-medium text-sm font-primary">{$i18n.t('Settings')}</div>
				</div>
			</button>
		</div>

		{#if enabledModels.length > 0}
			<div class="px-1.5 mt-3">
				<div class="px-1 w-full text-xs text-gray-500 dark:text-gray-500 font-medium pb-1.5">
					{$i18n.t('Models')}
				</div>
				<div class="flex flex-col">
					{#each enabledModels as model (model.id)}
						<div
							class="group flex items-center w-full rounded-lg hover:bg-sidebar-hover transition text-gray-800 dark:text-gray-200"
						>
							<button
								class="pl-2 py-1.5 flex items-center gap-2 flex-1 min-w-0"
								on:click={() => modelChatHandler(model.id)}
							>
								<img
									src={model?.info?.meta?.profile_image_url ?? DEFAULT_MODEL_IMAGE_URL}
									class="size-5 rounded-full shrink-0"
									alt={model.name}
									draggable="false"
								/>
								<div class="self-center font-medium text-sm font-primary truncate">
									{model.name}
								</div>
							</button>

							<!-- 右端: 編集(pencil)。hover で出現。分の幅は常に確保するので文字と重ならない -->
							<a
								class="invisible group-hover:visible shrink-0 p-1 mr-1.5 rounded-lg text-gray-600 dark:text-gray-400 hover:bg-sidebar-selected transition"
								href="/workspace/models/edit?id={model.id}"
								draggable="false"
								aria-label={$i18n.t('Edit')}
								on:click={() => {
									if ($mobile) {
										showSidebar.set(false);
									}
								}}
							>
								<Pencil className="size-4" strokeWidth="2" />
							</a>
						</div>
					{/each}
				</div>
			</div>
		{/if}

		<div class="relative flex flex-col flex-1 overflow-y-auto overflow-x-hidden mt-3 space-y-3">
			{#if !search && $pinnedChats.length > 0}
				<div class="flex flex-col space-y-1 rounded-xl">
					<Folder
						className=""
						bind:open={showPinnedChat}
						on:change={(e) => {
							localStorage.setItem('showPinnedChat', e.detail);
							console.log(e.detail);
						}}
						on:import={(e) => {
							importChatHandler(e.detail, true);
						}}
						on:drop={async (e) => {
							const { type, id, item } = e.detail;

							if (type === 'chat') {
								let chat = await getChatById(localStorage.token, id).catch((error) => {
									return null;
								});
								if (!chat && item) {
									chat = await importChat(localStorage.token, item.chat, item?.meta ?? {});
								}

								if (chat) {
									console.log(chat);
									if (chat.folder_id) {
										const res = await updateChatFolderIdById(
											localStorage.token,
											chat.id,
											null
										).catch((error) => {
											toast.error(`${error}`);
											return null;
										});
									}

									if (!chat.pinned) {
										const res = await toggleChatPinnedStatusById(localStorage.token, chat.id);
									}

									initChatList();
								}
							}
						}}
						name={$i18n.t('Pinned')}
					>
						<div
							class="ml-3 pl-1 mt-[1px] flex flex-col overflow-y-auto scrollbar-hidden border-s border-gray-100 dark:border-gray-900"
						>
							{#each $pinnedChats as chat, idx}
								<ChatItem
									id={chat.id}
									className="text-sm"
									title={chat.title}
									{shiftKey}
									selected={selectedChatId === chat.id}
									on:select={() => {
										selectedChatId = chat.id;
									}}
									on:unselect={() => {
										selectedChatId = null;
									}}
									on:change={async () => {
										initChatList();
									}}
								/>
							{/each}
						</div>
					</Folder>
				</div>
			{/if}

			{#if !search && folders}
				<Folders
					{folders}
					on:import={(e) => {
						const { folderId, items } = e.detail;
						importChatHandler(items, false, folderId);
					}}
					on:update={async (e) => {
						initChatList();
					}}
					on:change={async () => {
						initChatList();
					}}
				/>
			{/if}

			<div class="px-1.5 flex-1 flex flex-col overflow-y-auto scrollbar-hidden">
				{#if $chats}
					{#each $chats as chat, idx}
						{#if idx === 0 || (idx > 0 && chat.time_range !== $chats[idx - 1].time_range)}
							<div
								class="px-1 py-1.5 w-full text-xs text-gray-500 dark:text-gray-500 font-medium
										{idx > 0 ? 'mt-1.5' : ''}"
							>
								{$i18n.t(chat.time_range)}
								<!-- localisation keys for time_range to be recognized from the i18next parser (so they don't get automatically removed):
											{$i18n.t('Today')}
											{$i18n.t('Yesterday')}
											{$i18n.t('Previous 7 days')}
											{$i18n.t('Previous 30 days')}
											{$i18n.t('January')}
											{$i18n.t('February')}
											{$i18n.t('March')}
											{$i18n.t('April')}
											{$i18n.t('May')}
											{$i18n.t('June')}
											{$i18n.t('July')}
											{$i18n.t('August')}
											{$i18n.t('September')}
											{$i18n.t('October')}
											{$i18n.t('November')}
											{$i18n.t('December')}
											-->
							</div>
						{/if}

						<ChatItem
							className="text-sm"
							id={chat.id}
							title={chat.title}
							{shiftKey}
							selected={selectedChatId === chat.id}
							on:select={() => {
								selectedChatId = chat.id;
							}}
							on:unselect={() => {
								selectedChatId = null;
							}}
							on:change={async () => {
								initChatList();
							}}
						/>
					{/each}

					{#if $scrollPaginationEnabled && !allChatsLoaded}
						<Loader
							on:visible={(e) => {
								if (!chatListLoading) {
									loadMoreChats();
								}
							}}
						>
							<div class="w-full flex justify-center py-1 text-xs animate-pulse items-center gap-2">
								<Spinner className=" size-4" />
								<div class=" ">Loading...</div>
							</div>
						</Loader>
					{/if}
				{:else}
					<div class="w-full flex justify-center py-1 text-xs animate-pulse items-center gap-2">
						<Spinner className=" size-4" />
						<div class=" ">Loading...</div>
					</div>
				{/if}
			</div>
		</div>

		<div class="px-2">
			<div class="flex flex-col font-primary">
				{#if $user !== undefined && $user !== null}
					<UserMenu
						role={$user?.role}
						on:show={(e) => {
							if (e.detail === 'archived-chat') {
								showArchivedChats.set(true);
							}
						}}
					>
						<button
							class=" flex items-center rounded-xl py-2.5 px-2.5 w-full hover:bg-sidebar-hover transition"
							on:click={() => {
								showDropdown = !showDropdown;
							}}
						>
							<div class=" self-center mr-3">
								<img
									src={$user?.profile_image_url}
									class=" max-w-[30px] object-cover rounded-full"
									alt="User profile"
								/>
							</div>
							<div class=" self-center font-medium">{$user?.name}</div>
						</button>
					</UserMenu>
				{/if}
			</div>
		</div>
	</div>
</div>

<style>
	.scrollbar-hidden:active::-webkit-scrollbar-thumb,
	.scrollbar-hidden:focus::-webkit-scrollbar-thumb,
	.scrollbar-hidden:hover::-webkit-scrollbar-thumb {
		visibility: visible;
	}
	.scrollbar-hidden::-webkit-scrollbar-thumb {
		visibility: hidden;
	}
</style>
