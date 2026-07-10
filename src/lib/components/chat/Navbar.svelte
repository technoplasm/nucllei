<script lang="ts">
	import { getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import {
		WEBUI_NAME,
		banners,
		chatId,
		config,
		mobile,
		settings,
		showArchivedChats,
		showSidebar,
		theme,
		user
	} from '$lib/stores';
	import { themeChangeHandler } from '$lib/utils/theme';

	import { slide } from 'svelte/transition';
	import { page } from '$app/stores';

	import ShareChatModal from '../chat/ShareChatModal.svelte';
	import ModelSelector from '../chat/ModelSelector.svelte';
	import Tooltip from '../common/Tooltip.svelte';
	import Menu from '$lib/components/layout/Navbar/Menu.svelte';
	import UserMenu from '$lib/components/layout/Sidebar/UserMenu.svelte';
	import MenuLines from '../icons/MenuLines.svelte';
	import AdjustmentsHorizontal from '../icons/AdjustmentsHorizontal.svelte';

	import PencilSquare from '../icons/PencilSquare.svelte';
	import Banner from '../common/Banner.svelte';
	import Sun from '../icons/Sun.svelte';
	import Moon from '../icons/Moon.svelte';

	const i18n = getContext('i18n');

	// 'system' は matchMedia で解決、それ以外は名前に 'dark' を含むかで判定
	$: isDark =
		$theme === 'system'
			? typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches
			: ($theme ?? '').includes('dark');

	const toggleTheme = () => {
		themeChangeHandler(isDark ? 'light' : 'dark');
	};

	export let initNewChat: Function;
	export let title: string = $WEBUI_NAME;
	export let shareEnabled: boolean = false;

	export let chat;
	export let history;
	export let selectedModels;
	export let showModelSelector = true;

	let showShareChatModal = false;
	let showDownloadChatModal = false;
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
						: ''} mr-1 self-start flex flex-none items-center text-gray-600 dark:text-gray-400"
				>
					<button
						id="sidebar-toggle-button"
						class="cursor-pointer px-2 py-2 flex rounded-xl hover:bg-gray-50 dark:hover:bg-gray-850 transition"
						on:click={() => {
							showSidebar.set(!$showSidebar);
						}}
						aria-label="Toggle Sidebar"
					>
						<div class=" m-auto self-center">
							<MenuLines />
						</div>
					</button>
				</div>

				<div
					class="flex-1 overflow-hidden max-w-full py-0.5
			{$showSidebar ? 'ml-1' : ''}
			"
				>
					{#if showModelSelector}
						<ModelSelector bind:selectedModels />
					{/if}
				</div>

				<div class="self-start flex flex-none items-center text-gray-600 dark:text-gray-400">
					<!-- <div class="md:hidden flex self-center w-[1px] h-5 mx-2 bg-gray-300 dark:bg-stone-700" /> -->
					{#if shareEnabled && chat && chat.id}
						<Menu
							{chat}
							{shareEnabled}
							shareHandler={() => {
								showShareChatModal = !showShareChatModal;
							}}
							downloadHandler={() => {
								showDownloadChatModal = !showDownloadChatModal;
							}}
						>
							<button
								class="flex cursor-pointer px-2 py-2 rounded-xl hover:bg-gray-50 dark:hover:bg-gray-850 transition"
								id="chat-context-menu-button"
							>
								<div class=" m-auto self-center">
									<svg
										xmlns="http://www.w3.org/2000/svg"
										fill="none"
										viewBox="0 0 24 24"
										stroke-width="1.5"
										stroke="currentColor"
										class="size-5"
									>
										<path
											stroke-linecap="round"
											stroke-linejoin="round"
											d="M6.75 12a.75.75 0 1 1-1.5 0 .75.75 0 0 1 1.5 0ZM12.75 12a.75.75 0 1 1-1.5 0 .75.75 0 0 1 1.5 0ZM18.75 12a.75.75 0 1 1-1.5 0 .75.75 0 0 1 1.5 0Z"
										/>
									</svg>
								</div>
							</button>
						</Menu>
					{/if}

					<Tooltip content={$i18n.t('New Chat')}>
						<button
							id="new-chat-button"
							class=" flex {$showSidebar
								? 'md:hidden'
								: ''} cursor-pointer px-2 py-2 rounded-xl text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-850 transition"
							on:click={() => {
								initNewChat();
							}}
							aria-label="New Chat"
						>
							<div class=" m-auto self-center">
								<PencilSquare className=" size-5" strokeWidth="2" />
							</div>
						</button>
					</Tooltip>

					<Tooltip content={isDark ? $i18n.t('Light mode') : $i18n.t('Dark mode')}>
						<button
							id="theme-toggle-button"
							class="flex cursor-pointer px-2 py-2 rounded-xl text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-850 transition"
							on:click={toggleTheme}
							aria-label={isDark ? $i18n.t('Light mode') : $i18n.t('Dark mode')}
						>
							<div class=" m-auto self-center">
								{#if isDark}
									<Sun className=" size-5" strokeWidth="1.5" />
								{:else}
									<Moon className=" size-5" strokeWidth="1.5" />
								{/if}
							</div>
						</button>
					</Tooltip>

					{#if $user !== undefined && $user !== null}
						<UserMenu
							className="max-w-[200px]"
							role={$user?.role}
							on:show={(e) => {
								if (e.detail === 'archived-chat') {
									showArchivedChats.set(true);
								}
							}}
						>
							<button
								class="select-none flex rounded-xl p-1.5 w-full hover:bg-gray-50 dark:hover:bg-gray-850 transition"
								aria-label="User Menu"
							>
								<div class=" self-center">
									<img
										src={$user?.profile_image_url}
										class="size-6 object-cover rounded-full"
										alt="User profile"
										draggable="false"
									/>
								</div>
							</button>
						</UserMenu>
					{/if}
				</div>
			</div>
		</div>
	</div>

	{#if !history.currentId && !$chatId && ($banners.length > 0 || ($config?.license_metadata?.type ?? null) === 'trial' || (($config?.license_metadata?.seats ?? null) !== null && $config?.user_count > $config?.license_metadata?.seats))}
		<div class=" w-full z-30 mt-5">
			<div class=" flex flex-col gap-1 w-full">
				{#if ($config?.license_metadata?.type ?? null) === 'trial'}
					<Banner
						banner={{
							type: 'info',
							title: 'Trial License',
							content: $i18n.t(
								'You are currently using a trial license. Please contact support to upgrade your license.'
							)
						}}
					/>
				{/if}

				{#if ($config?.license_metadata?.seats ?? null) !== null && $config?.user_count > $config?.license_metadata?.seats}
					<Banner
						banner={{
							type: 'error',
							title: 'License Error',
							content: $i18n.t(
								'Exceeded the number of seats in your license. Please contact support to increase the number of seats.'
							)
						}}
					/>
				{/if}

				{#each $banners.filter( (b) => (b.dismissible ? !JSON.parse(localStorage.getItem('dismissedBannerIds') ?? '[]').includes(b.id) : true) ) as banner}
					<Banner
						{banner}
						on:dismiss={(e) => {
							const bannerId = e.detail;

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
