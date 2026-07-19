<script lang="ts">
	import { tick, getContext } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';

	import dayjs from 'dayjs';

	import { settings, chatId, WEBUI_NAME, models, user as sessionUser } from '$lib/stores';
	import { createMessagesList } from '$lib/utils';

	import { getChatByShareId, cloneSharedChatById } from '$lib/apis/chats';
	import type { ChatHistory, ChatMessage } from '$lib/types/chat';

	import Messages from '$lib/components/chat/Messages.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	import { getUserById, getUserSettings } from '$lib/apis/users';
	import { getModels } from '$lib/apis';
	import { toast } from 'svelte-sonner';
	import localizedFormat from 'dayjs/plugin/localizedFormat';

	const i18n = getContext('i18n');
	dayjs.extend(localizedFormat);

	let loaded = $state(false);

	let autoScroll = $state(true);

	let selectedModels = $state(['']);

	let chat = $state<{
		id: string;
		user_id: string;
		chat: {
			title: string;
			timestamp?: number;
			models?: string[];
			history?: ChatHistory;
			messages?: ChatMessage[];
		};
	} | null>(null);
	let user = $state<{ name: string; profile_image_url?: string } | undefined>(undefined);

	let title = $state('');
	const files: unknown[] = [];

	// Messages が表示ウィンドウ分を bind で書き戻す受け皿(こちらからは書かない)
	let messages = $state<ChatMessage[]>([]);
	let history = $state<ChatHistory>({
		messages: {},
		currentId: null
	});

	//////////////////////////
	// Web functions
	//////////////////////////

	const loadSharedChat = async () => {
		await chatId.set($page.params.id ?? '');

		// 依存のない 3 フェッチは並列に(直列だと空白時間 = 3 往復分になる)
		const [userSettings, modelList, _chat] = await Promise.all([
			getUserSettings().catch((error) => {
				console.error(error);
				return null;
			}),
			getModels(),
			getChatByShareId($chatId).catch(async () => {
				await goto('/');
				return null;
			})
		]);

		if (userSettings) {
			settings.set(userSettings.ui);
		} else {
			let localStorageSettings = {} as Parameters<(typeof settings)['set']>[0];

			try {
				localStorageSettings = JSON.parse(localStorage.getItem('settings') ?? '{}');
			} catch (e: unknown) {
				console.error('Failed to parse settings from localStorage', e);
			}

			settings.set(localStorageSettings);
		}

		await models.set(modelList);
		chat = _chat;

		if (chat) {
			user = await getUserById(chat.user_id).catch((error) => {
				console.error(error);
				return undefined;
			});

			const chatContent = chat.chat;

			if (chatContent?.history) {
				selectedModels = chatContent.models ?? [''];
				history = chatContent.history;
				title = chatContent.title;

				autoScroll = true;
				await tick();

				// Messages マウント前なのでここで直接末端を引く(旧 $: messages ミラーの代替)
				const lastMessage = createMessagesList(history, history.currentId).at(-1);
				if (lastMessage) {
					history.messages[lastMessage.id].done = true;
				}
				await tick();

				return true;
			} else {
				return null;
			}
		}
	};

	// share id の追従。初期値は init 時に処理し、以後の変化だけ $effect で拾う
	// (Chat.svelte の chatIdProp と同じ実行順パターン)
	let loadedShareId: string | null = null;
	const handleShareIdChange = () => {
		const id = $page.params.id;
		if (!id || loadedShareId === id) {
			return;
		}
		loadedShareId = id;

		(async () => {
			if (await loadSharedChat()) {
				await tick();
				loaded = true;
			} else {
				await goto('/');
			}
		})();
	};

	const cloneSharedChat = async () => {
		if (!chat) return;

		const res = await cloneSharedChatById(chat.id).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			goto(`/c/${res.id}`);
		}
	};

	handleShareIdChange();
	$effect(() => {
		void $page.params.id;
		handleShareIdChange();
	});
</script>

<svelte:head>
	<title>
		{title
			? `${title.length > 30 ? `${title.slice(0, 30)}...` : title} | ${$WEBUI_NAME}`
			: `${$WEBUI_NAME}`}
	</title>
</svelte:head>

{#if loaded}
	<div class="h-screen max-h-[100dvh] w-full flex flex-col text-content bg-white dark:bg-gray-900">
		<div class="flex flex-col flex-auto justify-center relative">
			<div class=" flex flex-col w-full flex-auto overflow-auto h-0" id="messages-container">
				<div
					class="pt-5 px-2 w-full {($settings?.widescreenMode ?? null)
						? 'max-w-full'
						: 'max-w-5xl'} mx-auto"
				>
					<div class="px-3">
						<div class=" text-2xl font-semibold line-clamp-1">
							{title}
						</div>

						<div class="flex text-sm justify-between items-center mt-1">
							<div class="text-content-muted">
								{dayjs(chat?.chat?.timestamp).format('LLL')}
							</div>
						</div>
					</div>
				</div>

				<div class=" h-full w-full flex flex-col py-2">
					<div class="w-full">
						<Messages
							className="h-full flex pt-4 pb-8 "
							{user}
							chatId={$chatId}
							readOnly={true}
							{selectedModels}
							bind:history
							bind:messages
							bind:autoScroll
							bottomPadding={files.length > 0}
							sendPrompt={() => {}}
							continueResponse={() => {}}
							regenerateResponse={() => {}}
						/>
					</div>
				</div>
			</div>

			{#if $sessionUser?.role === 'admin' || ($sessionUser?.permissions?.chat?.clone ?? false)}
				<div
					class="absolute bottom-0 right-0 left-0 flex justify-center w-full bg-linear-to-b from-transparent to-white dark:to-gray-900"
				>
					<div class="pb-5">
						<button
							class="px-4 py-2 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
							onclick={cloneSharedChat}
						>
							{$i18n.t('Clone Chat')}
						</button>
					</div>
				</div>
			{/if}
		</div>
	</div>
{:else}
	<!-- (app) レイアウト外なので遷移直後は全体が空になる — データ到着までスピナーで埋める -->
	<div
		class="h-screen max-h-[100dvh] w-full flex items-center justify-center text-content bg-white dark:bg-gray-900"
	>
		<Spinner />
	</div>
{/if}
