<script lang="ts">
	import { getContext } from 'svelte';

	import {
		banners,
		chatId,
		showArchivedChats,
		showControls,
		showSidebar,
		theme,
		user
	} from '$lib/stores';
	import { themeChangeHandler } from '$lib/utils/theme';
	import { collectChatFiles } from '$lib/utils/chat-files';
	import type { ChatFile } from '$lib/types/chat';

	import ShareChatModal from '$lib/components/chat/ShareChatModal.svelte';
	import ModelSelector from './ModelSelector.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Menu from './NavbarMenu.svelte';
	import UserMenu from '$lib/components/layout/Sidebar/UserMenu.svelte';
	import IdleTimeoutRing from '$lib/components/layout/IdleTimeoutRing.svelte';
	import Banner from '$lib/components/common/Banner.svelte';

	const i18n = getContext('i18n');

	let {
		initNewChat,
		shareEnabled = false,
		chat,
		history,
		chatFiles = [],
		selectedModels = $bindable()
	}: {
		initNewChat: () => void;
		shareEnabled?: boolean;
		chat: any;
		history: any;
		chatFiles?: ChatFile[];
		selectedModels: string[];
	} = $props();

	// 「このチャットのファイル」アイコンのバッジ数。右ペイン(ChatFiles.svelte)と
	// 同じ収集(chat レベル添付 + メッセージ画像・tool 生成ファイル)なので、
	// run_python 等が chat:message:files で添付した時点で history 経由で live に増える
	const chatFileCount = $derived(collectChatFiles(chatFiles, history).length);

	// 'system' は matchMedia で解決、それ以外は名前に 'dark' を含むかで判定
	const isDark = $derived(
		$theme === 'system'
			? typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches
			: ($theme ?? '').includes('dark')
	);

	const toggleTheme = () => {
		themeChangeHandler(isDark ? 'light' : 'dark');
	};

	let showShareChatModal = $state(false);
</script>

<ShareChatModal bind:show={showShareChatModal} chatId={$chatId} />

<nav class="sticky top-0 z-30 w-full py-1.5 -mb-8 flex flex-col items-center drag-region">
	<div class="flex items-center w-full px-1.5">
		<div
			class=" bg-linear-to-b via-50% from-surface via-surface to-transparent pointer-events-none absolute inset-0 -bottom-7 z-[-1]"
		></div>

		<div class=" flex max-w-full w-full mx-auto px-1 pt-0.5 bg-transparent">
			<div class="flex items-center w-full max-w-full">
				<div
					class="{$showSidebar
						? 'md:hidden'
						: ''} mr-1 self-start flex flex-none items-center text-content-muted"
				>
					<button
						id="sidebar-toggle-button"
						class="cursor-pointer px-2 py-2 flex rounded-xl hover:bg-surface-hover"
						onclick={() => {
							showSidebar.set(!$showSidebar);
						}}
						aria-label={$i18n.t('Toggle sidebar')}
					>
						<span class="i-lucide-menu size-5 block m-auto self-center"></span>
					</button>
				</div>

				<div class="flex-1 overflow-hidden max-w-full py-0.5 {$showSidebar ? 'ml-1' : ''}">
					<ModelSelector bind:selectedModels />
				</div>

				<div class="self-start flex flex-none items-center text-content-muted">
					<!-- アイドルタイムアウトまでの残り時間(リング + m:ss)。右上のボタン群の左端 = 「…」の左。
					     隠れている間も枠は取ったまま(opacity だけ)なので、出し入れで並びは動かない -->
					<IdleTimeoutRing />

					{#if shareEnabled && chat && chat.id}
						<Menu
							{chat}
							shareHandler={() => {
								showShareChatModal = !showShareChatModal;
							}}
						>
							<button
								class="flex cursor-pointer px-2 py-2 rounded-xl hover:bg-surface-hover"
								id="chat-context-menu-button"
								aria-label={$i18n.t('More')}
							>
								<span class="i-lucide-ellipsis size-5 block m-auto self-center"></span>
							</button>
						</Menu>
					{/if}

					<!-- llens(#69→#19→再改訂): 右ペイン(このチャットのファイル)のトグル。
					     「…」メニュー内では見つけにくいため Navbar 直置きへ戻し、
					     関連ファイル数をバッジで出す(0 件はバッジ非表示)。
					     ChatControls は !chatId で強制クローズするため、chat 未確定時は出さない -->
					{#if chat?.id}
						<Tooltip content={$i18n.t('Files in this chat')}>
							<button
								id="chat-files-button"
								class="relative flex cursor-pointer px-2 py-2 rounded-xl hover:bg-surface-hover"
								onclick={() => {
									showControls.set(!$showControls);
								}}
								aria-label={$i18n.t('Files in this chat')}
							>
								<span class="i-lucide-folder-open size-5 block m-auto self-center"></span>
								{#if chatFileCount > 0}
									<span
										class="absolute top-0.5 right-0 min-w-4 h-4 px-1 rounded-full bg-primary text-primary-content text-[10px] leading-4 font-medium text-center tabular-nums pointer-events-none"
									>
										{chatFileCount}
									</span>
								{/if}
							</button>
						</Tooltip>
					{/if}

					<Tooltip content={$i18n.t('New Chat')}>
						<button
							id="new-chat-button"
							class="flex {$showSidebar
								? 'md:hidden'
								: ''} cursor-pointer px-2 py-2 rounded-xl text-content-muted hover:bg-surface-hover"
							onclick={() => {
								initNewChat();
							}}
							aria-label={$i18n.t('New Chat')}
						>
							<span class="i-lucide-square-pen size-5 block m-auto self-center"></span>
						</button>
					</Tooltip>

					<Tooltip content={isDark ? $i18n.t('Light mode') : $i18n.t('Dark mode')}>
						<button
							id="theme-toggle-button"
							class="flex cursor-pointer px-2 py-2 rounded-xl text-content-muted hover:bg-surface-hover"
							onclick={toggleTheme}
							aria-label={isDark ? $i18n.t('Light mode') : $i18n.t('Dark mode')}
						>
							{#if isDark}
								<span class="i-lucide-sun size-5 block m-auto self-center"></span>
							{:else}
								<span class="i-lucide-moon size-5 block m-auto self-center"></span>
							{/if}
						</button>
					</Tooltip>

					{#if $user !== undefined && $user !== null}
						<UserMenu
							className="max-w-[200px]"
							onShow={(name) => {
								if (name === 'archived-chat') {
									showArchivedChats.set(true);
								}
							}}
						>
							<button
								class="select-none flex rounded-xl p-1.5 w-full hover:bg-surface-hover"
								aria-label={$i18n.t('User Menu')}
							>
								<img
									src={$user?.profile_image_url}
									class="size-6 object-cover rounded-full self-center"
									alt="User profile"
									draggable="false"
								/>
							</button>
						</UserMenu>
					{/if}
				</div>
			</div>
		</div>
	</div>

	{#if !history.currentId && !$chatId && $banners.length > 0}
		<div class=" w-full z-30 mt-5">
			<div class=" flex flex-col gap-1 w-full">
				{#each $banners.filter( (b) => (b.dismissible ? !JSON.parse(localStorage.getItem('dismissedBannerIds') ?? '[]').includes(b.id) : true) ) as banner (banner.id)}
					<Banner
						{banner}
						onDismiss={(bannerId) => {
							localStorage.setItem(
								'dismissedBannerIds',
								JSON.stringify(
									[
										bannerId,
										...JSON.parse(localStorage.getItem('dismissedBannerIds') ?? '[]')
									].filter((id) => $banners.find((b) => b.id === id))
								)
							);
						}}
					/>
				{/each}
			</div>
		</div>
	{/if}
</nav>
