<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { DEFAULT_MODEL_IMAGE_URL } from '$lib/constants';

	import { goto } from '$app/navigation';
	import {
		WEBUI_NAME,
		user,
		chats,
		models,
		showSettings,
		chatId,
		showSidebar,
		mobile,
		showArchivedChats,
		pinnedChats,
		scrollPaginationEnabled,
		currentChatPage,
		isApp
	} from '$lib/stores';
	import { onMount, getContext, onDestroy } from 'svelte';

	const i18n = getContext('i18n');

	import {
		getChatList,
		getChatListBySearchText,
		getPinnedChatList,
		toggleChatPinnedStatusById,
		getChatById,
		importChat
	} from '$lib/apis/chats';

	import ArchivedChatsModal from './Sidebar/ArchivedChatsModal.svelte';
	import UserMenu from './Sidebar/UserMenu.svelte';
	import ChatItem from './Sidebar/ChatItem.svelte';
	import ChatSearch from './Sidebar/ChatSearch.svelte';
	import Spinner from '../common/Spinner.svelte';
	import Loader from '../common/Loader.svelte';
	import Folder from '../common/Folder.svelte';
	import { overlayScrollbar } from '$lib/actions/overlay-scrollbars';

	let navElement: HTMLDivElement | null = $state(null);

	// サイドバー単一スクロール: ヘッダ行(WEBUI_NAME)だけ sticky、残りは 1 本のスクロール。
	// 隠れたコンテンツがある辺にだけ影を出す(scroll + ResizeObserver で再判定)。
	let scrollContainer: HTMLDivElement | null = $state(null);
	let scrollContent: HTMLDivElement | null = $state(null);
	let showBottomShadow = $state(false);

	const updateScrollShadows = () => {
		const el = scrollContainer;
		if (!el) {
			return;
		}
		showBottomShadow = el.scrollTop + el.clientHeight < el.scrollHeight - 1;
	};

	$effect(() => {
		if (!scrollContainer || !scrollContent) {
			return;
		}
		updateScrollShadows();
		// コンテンツの非同期増減(チャット一覧のロード等)とコンテナ自体のリサイズの両方を拾う
		const resizeObserver = new ResizeObserver(updateScrollShadows);
		resizeObserver.observe(scrollContainer);
		resizeObserver.observe(scrollContent);
		return () => {
			resizeObserver.disconnect();
		};
	});

	// サイドバー「モデル」欄。$models(= preset のみ、#82)と連動して自動反映。
	// meta.hidden は生モデル管理(旧 Settings > Models)ごと廃止した死に概念 — 参照しない。
	const enabledModels = $derived(($models ?? []).filter((m) => m?.id));

	// モデル行クリック: そのモデルを選択した状態で新規チャットを開く(?models= を initNewChat が解釈)。
	const modelChatHandler = (modelId: string) =>
		newChatHandler(`/?models=${encodeURIComponent(modelId)}`);

	// 新規チャットの開始画面へ(ロゴ / 「新しいチャット」/ モデル行で共通)。goto だけでは足りない:
	// 開始画面から最初のメッセージを送ると URL は replaceState で /c/<id> になるが、SvelteKit 上の
	// ルートは / のまま — そこへの goto('/') は同一ルートへの遷移で Chat が再マウントされず、
	// チャットが表示され続ける。Navbar の new-chat-button(= Chat の initNewChat)まで押す
	const newChatHandler = async (url = '/') => {
		selectedChatId = null;
		// 検索は「過去を探す」、新しいチャットは「次を始める」— ここで文脈が切り替わるので
		// 検索語と絞り込みを消す (結果を開いただけでは消さない。ken 2026-10-03)
		searchInput = '';
		searchText = '';
		searchResults = null;
		await goto(url);
		const newChatButton = document.getElementById('new-chat-button');
		setTimeout(() => {
			newChatButton?.click();
			if ($mobile) {
				showSidebar.set(false);
			}
		}, 0);
	};

	// llens(#121): サイドバーのチャット検索。searchText が空でない間は通常一覧の代わりに
	// サーバ検索(タイトル + 本文の部分一致、`GET /chats/search`)の結果を出す。
	// ピン留め / 時期見出し / 無限スクロールは通常一覧だけのもので、結果一覧には出さない。
	let searchInput = $state(''); // 入力欄の値 (ChatSearch に bind — 新しいチャットで空にする)
	let searchText = $state('');
	let searchResults: { id: string; title: string }[] | null = $state(null);
	let searchSeq = 0;

	const searchHandler = async (text: string) => {
		searchText = text;
		const seq = ++searchSeq;
		if (!text) {
			searchResults = null;
			return;
		}
		const res = await getChatListBySearchText(text).catch(() => null);
		if (seq !== searchSeq) {
			return; // 古い応答(入力が進んでいる)
		}
		searchResults = res ?? [];
	};

	// 「チャットで調べる」: 検索語を含む依頼文を新規チャットの入力欄にプリフィルする(送信はしない —
	// 利用者が文面を直してから送る)。?draft= は Chat.initNewChat が解釈する(?q= は自動送信なので使わない)。
	// モデルは組み込みの履歴ツール(list_history / grep_history)で履歴を探す。
	const askInChatHandler = (text: string) => {
		const draft = $i18n.t('Search my chat history for "{{query}}"', { query: text });
		newChatHandler(`/?draft=${encodeURIComponent(draft)}`);
	};

	let shiftKey = $state(false);

	let selectedChatId: string | null = $state(null);
	let showPinnedChat = $state(true);

	// Pagination variables
	let chatListLoading = false;
	let allChatsLoaded = $state(false);

	const initChatList = async () => {
		// Reset pagination variables
		pinnedChats.set(await getPinnedChatList());

		currentChatPage.set(1);
		allChatsLoaded = false;

		await chats.set(await getChatList($currentChatPage));

		// Enable pagination
		scrollPaginationEnabled.set(true);
	};

	const loadMoreChats = async () => {
		chatListLoading = true;

		currentChatPage.set($currentChatPage + 1);

		const newChatList = await getChatList($currentChatPage);

		// once the bottom of the list has been reached (no results) there is no need to continue querying
		allChatsLoaded = newChatList.length === 0;
		await chats.set([...($chats ? $chats : []), ...newChatList]);

		chatListLoading = false;
	};

	const importChatHandler = async (items: any[], pinned = false) => {
		for (const item of items) {
			if (item.chat) {
				await importChat(item.chat, item?.meta ?? {}, pinned);
			}
		}

		initChatList();
	};

	const inputFilesHandler = async (files: File[]) => {
		for (const file of files) {
			const reader = new FileReader();
			reader.onload = async (e) => {
				const content = e.target?.result;

				try {
					const chatItems = JSON.parse(content as string);
					importChatHandler(chatItems);
				} catch {
					toast.error($i18n.t(`Invalid file format.`));
				}
			};

			reader.readAsText(file);
		}
	};

	const onDragOver = (e: DragEvent) => {
		e.preventDefault();
	};

	const onDragLeave = () => {};

	const onDrop = async (e: DragEvent) => {
		e.preventDefault();

		// Perform file drop check and handle it accordingly
		if (e.dataTransfer?.files) {
			const inputFiles = Array.from(e.dataTransfer.files);

			if (inputFiles && inputFiles.length > 0) {
				inputFilesHandler(inputFiles); // Handle the dropped files
			}
		}
	};

	let touchstart: Touch | null = null;
	let touchend: Touch | null = null;

	function checkDirection() {
		if (!touchstart || !touchend) {
			return;
		}
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

	const onTouchStart = (e: TouchEvent) => {
		touchstart = e.changedTouches[0];
	};

	const onTouchEnd = (e: TouchEvent) => {
		touchend = e.changedTouches[0];
		checkDirection();
	};

	const onKeyDown = (e: KeyboardEvent) => {
		if (e.key === 'Shift') {
			shiftKey = true;
		}
	};

	const onKeyUp = (e: KeyboardEvent) => {
		if (e.key === 'Shift') {
			shiftKey = false;
		}
	};

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
				const nav = document.getElementsByTagName('nav')[0];
				if (nav) {
					nav.style.setProperty('-webkit-app-region', 'drag');
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
			const nav = document.getElementsByTagName('nav')[0];

			if (nav) {
				if ($mobile) {
					if (!value) {
						nav.style.setProperty('-webkit-app-region', 'drag');
					} else {
						nav.style.setProperty('-webkit-app-region', 'no-drag');
					}
				} else {
					nav.style.setProperty('-webkit-app-region', 'drag');
				}
			}
		});

		await initChatList();

		window.addEventListener('keydown', onKeyDown);
		window.addEventListener('keyup', onKeyUp);

		window.addEventListener('touchstart', onTouchStart);
		window.addEventListener('touchend', onTouchEnd);

		window.addEventListener('blur', onBlur);

		navElement?.addEventListener('dragover', onDragOver);
		navElement?.addEventListener('drop', onDrop);
		navElement?.addEventListener('dragleave', onDragLeave);
	});

	onDestroy(() => {
		window.removeEventListener('keydown', onKeyDown);
		window.removeEventListener('keyup', onKeyUp);

		window.removeEventListener('touchstart', onTouchStart);
		window.removeEventListener('touchend', onTouchEnd);

		window.removeEventListener('blur', onBlur);

		navElement?.removeEventListener('dragover', onDragOver);
		navElement?.removeEventListener('drop', onDrop);
		navElement?.removeEventListener('dragleave', onDragLeave);
	});
</script>

<ArchivedChatsModal
	bind:show={$showArchivedChats}
	onChange={async () => {
		await initChatList();
	}}
/>

<!-- svelte-ignore a11y_no_static_element_interactions -->

{#if $showSidebar}
	<div
		class=" {$isApp
			? ' ml-[4.5rem] md:ml-0'
			: ''} fixed md:hidden z-40 top-0 right-0 left-0 bottom-0 bg-black/60 w-full min-h-screen h-screen flex justify-center overflow-hidden overscroll-contain"
		onmousedown={() => {
			showSidebar.set(!$showSidebar);
		}}
	></div>
{/if}

<div
	bind:this={navElement}
	id="sidebar"
	class="h-screen max-h-[100dvh] min-h-screen select-none {$showSidebar
		? 'md:relative w-[var(--sidebar-width)] max-w-[var(--sidebar-width)]'
		: '-translate-x-[var(--sidebar-width)] w-[0px]'} {$isApp
		? `ml-[4.5rem] md:ml-0 `
		: 'transition-width duration-200 ease-in-out'}  shrink-0 bg-surface-sidebar text-content fixed z-50 top-0 left-0 overflow-x-hidden"
	data-state={$showSidebar}
>
	<div
		class="relative flex flex-col h-screen max-h-[100dvh] w-[var(--sidebar-width)] overflow-x-hidden z-50 {$showSidebar
			? ''
			: 'invisible'}"
	>
		<!-- OverlayScrollbars: ラッパ=ホスト / 既存スクロール要素=viewport(childViewport)。
		 影ロジック(updateScrollShadows)は viewport の scroll のまま無改修で生きる -->
		<div class="flex-1 min-h-0" use:overlayScrollbar={{ childViewport: true }}>
			<div
				bind:this={scrollContainer}
				onscroll={updateScrollShadows}
				class="h-full overflow-y-auto overflow-x-hidden"
			>
				<div bind:this={scrollContent} class="min-h-full flex flex-col">
					<div
						class="sticky top-0 z-20 shrink-0 pt-1 px-1.5 flex justify-between items-center gap-1 text-content-muted"
					>
						<!-- Navbar と同じ方式: ヘッダ自体は透明、背後の縦グラデでスクロール内容をフェードさせる -->
						<div
							class="bg-linear-to-b via-50% from-surface-sidebar via-surface-sidebar to-transparent pointer-events-none absolute inset-0 -bottom-2 z-[-1]"
						></div>
						<a
							class="px-1 py-2 flex items-center gap-1.5 min-w-0 flex-1 rounded-lg no-drag-region"
							href="/"
							draggable="false"
							onclick={() => newChatHandler()}
						>
							<img
								crossorigin="anonymous"
								src="/static/favicon.png"
								class="size-6 rounded-full shrink-0"
								alt="logo"
							/>
							<div class="self-center font-medium text-md text-content font-primary truncate">
								{$WEBUI_NAME}
							</div>
						</a>

						<button
							class=" cursor-pointer p-[7px] flex rounded-xl hover:bg-sidebar-hover shrink-0"
							onclick={() => {
								showSidebar.set(!$showSidebar);
							}}
							aria-label="Toggle Sidebar"
						>
							<span class="i-lucide-menu size-5 block m-auto self-center"></span>
						</button>
					</div>

					<div class="px-1.5 flex justify-center text-content mt-0.5">
						<a
							id="sidebar-new-chat-button"
							class=" px-2 py-1.5 grow flex items-center space-x-3 rounded-lg hover:bg-sidebar-hover no-drag-region"
							href="/"
							draggable="false"
							onclick={() => newChatHandler()}
						>
							<span class="i-lucide-square-pen size-[1.1rem] block self-center"></span>
							<div class="flex self-center translate-y-[0.5px]">
								<div class=" self-center font-medium text-sm font-primary">
									{$i18n.t('New Chat')}
								</div>
							</div>
						</a>
					</div>

					<!-- llens: ワークスペースは全ログインユーザーに表示(#69 のファイル管理を
		     /workspace/files へ移設 — 非 admin もファイルタブに入れる) -->
					<div class="px-1.5 flex justify-center text-content">
						<a
							class="px-2 py-1.5 grow flex items-center space-x-3 rounded-lg hover:bg-sidebar-hover"
							href="/workspace"
							onclick={() => {
								selectedChatId = null;
								chatId.set('');

								if ($mobile) {
									showSidebar.set(false);
								}
							}}
							draggable="false"
						>
							<span class="i-lucide-layout-grid size-[1.1rem] block self-center"></span>
							<div class="flex self-center translate-y-[0.5px]">
								<div class=" self-center font-medium text-sm font-primary">
									{$i18n.t('Workspace')}
								</div>
							</div>
						</a>
					</div>

					<div class="px-1.5 flex justify-center text-content">
						<button
							class="px-2 py-1.5 grow flex items-center space-x-3 rounded-lg hover:bg-sidebar-hover"
							onclick={async () => {
								await showSettings.set(true);
								if ($mobile) {
									showSidebar.set(false);
								}
							}}
						>
							<span class="i-lucide-settings size-[1.1rem] block self-center"></span>
							<div class="flex self-center translate-y-[0.5px]">
								<div class=" self-center font-medium text-sm font-primary">
									{$i18n.t('Settings')}
								</div>
							</div>
						</button>
					</div>

					{#if enabledModels.length > 0}
						<div class="px-1.5 mt-3">
							<div class="px-1 w-full text-xs text-content-muted font-medium pb-1.5">
								{$i18n.t('Models')}
							</div>
							<div class="flex flex-col">
								{#each enabledModels as model (model.id)}
									<div
										class="group flex items-center w-full rounded-lg hover:bg-sidebar-hover text-content"
									>
										<button
											class="pl-2 py-1.5 flex items-center gap-2 flex-1 min-w-0"
											onclick={() => modelChatHandler(model.id)}
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

										<!-- 右端: 編集(pencil)。admin のみ(#133 — 遷移先の /workspace/models は非 admin を弾く)。hover で出現。分の幅は常に確保するので文字と重ならない -->
										{#if $user?.role === 'admin'}
											<a
												class="invisible group-hover:visible shrink-0 p-1 mr-1.5 rounded-lg text-content-muted hover:bg-sidebar-selected"
												href="/workspace/models/edit?id={model.id}"
												draggable="false"
												aria-label={$i18n.t('Edit')}
												onclick={() => {
													if ($mobile) {
														showSidebar.set(false);
													}
												}}
											>
												<span class="i-lucide-pencil size-4 block"></span>
											</a>
										{/if}
									</div>
								{/each}
							</div>
						</div>
					{/if}

					<!-- llens(#121): チャット検索(一覧の直上)。入力中は下の一覧が検索結果に切り替わる -->
					<div class="mt-3">
						<ChatSearch
							bind:value={searchInput}
							active={!!searchText}
							onSearch={searchHandler}
							onAsk={askInChatHandler}
						/>
					</div>

					{#if searchText}
						<div class="px-1.5 flex-1 flex flex-col mt-1.5">
							{#if searchResults === null}
								<div
									class="w-full flex justify-center py-1 text-xs animate-pulse items-center gap-2"
								>
									<Spinner className=" size-4" />
									<div class=" ">Loading...</div>
								</div>
							{:else if searchResults.length === 0}
								<div class="px-1 py-1.5 w-full text-xs text-content-muted">
									{$i18n.t('No matching chats')}
								</div>
							{:else}
								{#each searchResults as chat (chat.id)}
									<ChatItem
										className="text-sm"
										id={chat.id}
										title={chat.title}
										{shiftKey}
										selected={selectedChatId === chat.id}
										onSelect={() => {
											selectedChatId = chat.id;
										}}
										onUnselect={() => {
											selectedChatId = null;
										}}
										onChange={() => {
											initChatList();
											searchHandler(searchText);
										}}
									/>
								{/each}
							{/if}
						</div>
					{:else}
						<div class="flex flex-col flex-1 mt-1.5 space-y-3">
							{#if $pinnedChats.length > 0}
								<div class="flex flex-col space-y-1 rounded-xl">
									<Folder
										className=""
										bind:open={showPinnedChat}
										onChange={(open) => {
											localStorage.setItem('showPinnedChat', String(open));
										}}
										onImport={(items) => {
											importChatHandler(items, true);
										}}
										onDrop={async (data) => {
											const { type, id, item } = data;

											if (type === 'chat') {
												let chat = await getChatById(id).catch(() => {
													return null;
												});
												if (!chat && item) {
													chat = await importChat(item.chat, item?.meta ?? {});
												}

												if (chat) {
													if (!chat.pinned) {
														await toggleChatPinnedStatusById(chat.id);
													}

													initChatList();
												}
											}
										}}
										name={$i18n.t('Pinned')}
									>
										<div
											class="ml-3 pl-1 mt-[1px] flex flex-col border-s border-gray-100 dark:border-gray-900"
										>
											{#each $pinnedChats as chat (chat.id)}
												<ChatItem
													id={chat.id}
													className="text-sm"
													title={chat.title}
													{shiftKey}
													selected={selectedChatId === chat.id}
													onSelect={() => {
														selectedChatId = chat.id;
													}}
													onUnselect={() => {
														selectedChatId = null;
													}}
													onChange={() => {
														initChatList();
													}}
												/>
											{/each}
										</div>
									</Folder>
								</div>
							{/if}

							<div class="px-1.5 flex-1 flex flex-col">
								{#if $chats}
									{#each $chats as chat, idx (chat.id)}
										{#if idx === 0 || (idx > 0 && chat.time_range !== $chats[idx - 1].time_range)}
											<div
												class="px-1 py-1.5 w-full text-xs text-content-muted font-medium
										{idx > 0 ? 'mt-1.5' : ''}"
											>
												{$i18n.t(chat.time_range ?? '')}
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
											onSelect={() => {
												selectedChatId = chat.id;
											}}
											onUnselect={() => {
												selectedChatId = null;
											}}
											onChange={() => {
												initChatList();
											}}
										/>
									{/each}

									{#if $scrollPaginationEnabled && !allChatsLoaded}
										<Loader
											onVisible={() => {
												if (!chatListLoading) {
													loadMoreChats();
												}
											}}
										>
											<div
												class="w-full flex justify-center py-1 text-xs animate-pulse items-center gap-2"
											>
												<Spinner className=" size-4" />
												<div class=" ">Loading...</div>
											</div>
										</Loader>
									{/if}
								{:else}
									<div
										class="w-full flex justify-center py-1 text-xs animate-pulse items-center gap-2"
									>
										<Spinner className=" size-4" />
										<div class=" ">Loading...</div>
									</div>
								{/if}
							</div>
						</div>
					{/if}

					<!-- 下に隠れたコンテンツがあるときだけスクロール領域の下辺に出るフェード -->
					<div
						class="pointer-events-none sticky bottom-0 shrink-0 h-6 -mt-6 bg-linear-to-t from-surface-sidebar to-transparent transition-opacity duration-200 {showBottomShadow
							? 'opacity-100'
							: 'opacity-0'}"
					></div>
				</div>
			</div>
		</div>

		<div class="px-2 pb-1 shrink-0">
			<div class="flex flex-col font-primary">
				{#if $user !== undefined && $user !== null}
					<UserMenu
						onShow={(payload) => {
							if (payload === 'archived-chat') {
								showArchivedChats.set(true);
							}
						}}
					>
						<button
							class=" flex items-center rounded-xl py-2.5 px-2.5 w-full hover:bg-sidebar-hover"
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
