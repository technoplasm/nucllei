<script lang="ts">
	import { v4 as uuidv4 } from 'uuid';
	import { chats, settings, user as _user, currentChatPage } from '$lib/stores';
	import { tick, getContext } from 'svelte';

	import { toast } from 'svelte-sonner';
	import { getChatList, updateChatById } from '$lib/apis/chats';
	import type { Model } from '$lib/stores';
	import type { ChatHistory, ChatMessage } from '$lib/types/chat';

	import Message from './Messages/Message.svelte';
	import Loader from '../common/Loader.svelte';
	import Spinner from '../common/Spinner.svelte';

	import ChatPlaceholder from './ChatPlaceholder.svelte';

	const i18n = getContext('i18n');

	let {
		className = 'h-full flex pt-8',
		chatId = '',
		user = $_user,
		prompt = $bindable(),
		history = $bindable({ messages: {}, currentId: null }),
		selectedModels,
		atSelectedModel = undefined,
		messages = $bindable([]),
		sendPrompt,
		regenerateResponse,
		chatActionHandler = undefined,
		addMessages = () => {},
		readOnly = false,
		bottomPadding = false,
		autoScroll = $bindable(false)
	}: {
		className?: string;
		chatId?: string;
		user?: { name: string; profile_image_url?: string } | undefined;
		prompt?: string;
		history?: ChatHistory;
		selectedModels: string[];
		atSelectedModel?: Model | undefined;
		messages?: ChatMessage[];
		sendPrompt: (
			history: ChatHistory,
			prompt: string,
			parentId: string,
			opts?: { modelId?: string | null; newChat?: boolean }
		) => void | Promise<void>;
		regenerateResponse: (message: ChatMessage) => void | Promise<void>;
		chatActionHandler?: (
			chatId: string,
			actionId: string,
			modelId: string | undefined,
			messageId: string,
			event?: unknown
		) => void | Promise<void>;
		addMessages?: (data: {
			modelId: string;
			parentId: string;
			messages: { role: string; content: string }[];
		}) => void | Promise<void>;
		readOnly?: boolean;
		bottomPadding?: boolean;
		autoScroll?: boolean;
	} = $props();

	let messagesCount = $state(20);
	let messagesLoading = $state(false);

	const loadMoreMessages = async () => {
		// scroll slightly down to disable continuous loading
		const element = document.getElementById('messages-container');
		if (element) {
			element.scrollTop = element.scrollTop + 100;
		}

		messagesLoading = true;
		messagesCount += 20;

		await tick();

		messagesLoading = false;
	};

	$effect(() => {
		if (history.currentId) {
			const _messages: ChatMessage[] = [];

			let message: ChatMessage | null | undefined = history.messages[history.currentId];
			while (message && _messages.length <= messagesCount) {
				_messages.unshift({ ...message });
				message = message.parentId !== null ? history.messages[message.parentId] : null;
			}

			messages = _messages;
		} else {
			messages = [];
		}
	});

	$effect(() => {
		if (autoScroll && bottomPadding) {
			(async () => {
				await tick();
				scrollToBottom();
			})();
		}
	});

	const scrollToBottom = () => {
		const element = document.getElementById('messages-container');
		if (element) {
			element.scrollTop = element.scrollHeight;
		}
	};

	// history の深い変異(currentId 付け替え等)を bind:history 経由で親へ伝える。
	// Chat.svelte($state proxy)には no-op だが、legacy な親(s/[id])には必要。
	const notifyHistoryChange = () => {
		// eslint-disable-next-line no-self-assign
		history = history;
	};

	// #119: ローカル変異の前に取る巻き戻し用の写し(保存が拒否されたら updateChat がこれへ戻す)
	const snapshotHistory = () => $state.snapshot(history) as ChatHistory;

	// rollback: 保存失敗時に戻すローカル履歴。
	const updateChat = async (rollback: ChatHistory) => {
		// 観測 view(readOnly — /s/ 共有・#107 agent session)から誤って DB を書く
		// 事故の防壁。UI ゲート(ボタン非表示)に依存しない defense in depth。
		if (readOnly) return;
		notifyHistoryChange();
		await tick();
		try {
			await updateChatById(chatId, {
				history: history,
				messages: messages
			});
		} catch {
			// #119: 保存されなかった見かけを残さない — ローカルを変異前へ戻して知らせる
			// (以前は unhandled rejection で無言、再読み込みで初めて元に戻っていた)
			history = rollback;
			toast.error($i18n.t('Failed to save the chat.'));
			return;
		}

		currentChatPage.set(1);
		await chats.set(await getChatList($currentChatPage));
	};

	const scrollOnBranchChange = () => {
		if ($settings?.scrollOnBranchChange ?? true) {
			const element = document.getElementById('messages-container');
			if (element) {
				autoScroll = element.scrollHeight - element.scrollTop <= element.clientHeight + 50;
			}

			setTimeout(() => {
				scrollToBottom();
			}, 100);
		}
	};

	// 指定 messageId の枝の最深子まで降りる
	const deepestChildId = (messageId: string): string => {
		let currentId = messageId;
		let childrenIds = history.messages[currentId].childrenIds;
		while (childrenIds.length !== 0) {
			currentId = childrenIds.at(-1) as string;
			childrenIds = history.messages[currentId].childrenIds;
		}
		return currentId;
	};

	const rootMessageIds = () =>
		Object.values(history.messages)
			.filter((message) => message.parentId === null)
			.map((message) => message.id);

	const gotoMessage = async (message: ChatMessage, idx: number) => {
		// Determine the correct sibling list (either parent's children or root messages)
		const siblings =
			message.parentId !== null ? history.messages[message.parentId].childrenIds : rootMessageIds();

		// Clamp index to a valid range
		idx = Math.max(0, Math.min(idx, siblings.length - 1));

		const messageId = siblings[idx];

		// If we're navigating to a different message
		if (message.id !== messageId) {
			// Drill down to the deepest child of that branch
			history.currentId = deepestChildId(messageId);
			notifyHistoryChange();
		}

		await tick();

		// Optional auto-scroll
		scrollOnBranchChange();
	};

	const showPreviousMessage = async (message: ChatMessage) => {
		const siblings =
			message.parentId !== null ? history.messages[message.parentId].childrenIds : rootMessageIds();

		const messageId = siblings[Math.max(siblings.indexOf(message.id) - 1, 0)];

		if (message.id !== messageId) {
			history.currentId = deepestChildId(messageId);
			notifyHistoryChange();
		}

		await tick();

		scrollOnBranchChange();
	};

	const showNextMessage = async (message: ChatMessage) => {
		const siblings =
			message.parentId !== null ? history.messages[message.parentId].childrenIds : rootMessageIds();

		const messageId = siblings[Math.min(siblings.indexOf(message.id) + 1, siblings.length - 1)];

		if (message.id !== messageId) {
			history.currentId = deepestChildId(messageId);
			notifyHistoryChange();
		}

		await tick();

		scrollOnBranchChange();
	};

	// 履歴は append-only(#31): その場の書き換えとメッセージ削除はサーバが拒否するので、
	// 経路ごと持たない(#119 — 削除ボタンと「保存」を撤去)。
	// user = 兄弟ブランチとして送り直す / assistant = 兄弟ブランチとして複製保存する。元は残る。
	const editMessage = async (messageId: string, content: string) => {
		if (history.messages[messageId].role === 'user') {
			// New user message
			const userPrompt = content;
			const userMessageId = uuidv4();

			const userMessage: ChatMessage = {
				id: userMessageId,
				parentId: history.messages[messageId].parentId,
				childrenIds: [],
				role: 'user',
				content: userPrompt,
				...(history.messages[messageId].files && { files: history.messages[messageId].files }),
				timestamp: Math.floor(Date.now() / 1000) // Unix epoch
			};

			const messageParentId = history.messages[messageId].parentId;

			if (messageParentId !== null) {
				history.messages[messageParentId].childrenIds = [
					...history.messages[messageParentId].childrenIds,
					userMessageId
				];
			}

			history.messages[userMessageId] = userMessage;
			history.currentId = userMessageId;

			await tick();
			await sendPrompt(history, userPrompt, userMessageId);
		} else {
			// New response message
			const rollback = snapshotHistory();
			const responseMessageId = uuidv4();
			const message = history.messages[messageId];
			const parentId = message.parentId;

			const responseMessage: ChatMessage = {
				...message,
				id: responseMessageId,
				parentId: parentId,
				childrenIds: [],
				files: undefined,
				content: content,
				timestamp: Math.floor(Date.now() / 1000) // Unix epoch
			};

			history.messages[responseMessageId] = responseMessage;
			history.currentId = responseMessageId;

			// Append messageId to childrenIds of parent message
			if (parentId !== null) {
				history.messages[parentId].childrenIds = [
					...history.messages[parentId].childrenIds,
					responseMessageId
				];
			}

			await updateChat(rollback);
		}
	};

	const actionMessage = async (actionId: string, message: ChatMessage, event: unknown = null) => {
		await chatActionHandler?.(chatId, actionId, message.model, message.id, event);
	};
</script>

<div class={className}>
	<!-- 挨拶プレースホルダは入力できる文脈専用。readOnly(共有ビュー等)で空のときは何も出さない -->
	{#if !readOnly && Object.keys(history?.messages ?? {}).length == 0}
		<ChatPlaceholder
			modelIds={selectedModels}
			{atSelectedModel}
			submitPrompt={async (p) => {
				let text = p;

				if (p.includes('{{CLIPBOARD}}')) {
					const clipboardText = await navigator.clipboard.readText().catch(() => {
						toast.error($i18n.t('Failed to read clipboard contents'));
						return '{{CLIPBOARD}}';
					});

					text = p.replaceAll('{{CLIPBOARD}}', clipboardText);
				}

				prompt = text;
				await tick();
			}}
		/>
	{:else}
		<div class="w-full pt-2">
			{#key chatId}
				<div class="w-full">
					<!-- 「上にまだある」ローダー。空配列だと at(0)?.parentId が undefined !== null で
					 真になり無限 Loading になる(readOnly 空表示で露出 — 2026-07-19)ので長さも見る -->
					{#if messages.length > 0 && messages.at(0)?.parentId !== null}
						<Loader
							onVisible={() => {
								if (!messagesLoading) {
									loadMoreMessages();
								}
							}}
						>
							<div class="w-full flex justify-center py-1 text-xs animate-pulse items-center gap-2">
								<Spinner className=" size-4" />
								<div class=" ">Loading...</div>
							</div>
						</Loader>
					{/if}

					{#each messages as message (message.id)}
						<Message
							bind:history
							messageId={message.id}
							{user}
							{gotoMessage}
							{showPreviousMessage}
							{showNextMessage}
							{editMessage}
							{actionMessage}
							{regenerateResponse}
							{addMessages}
							{readOnly}
						/>
					{/each}
				</div>
				<div class="pb-16"></div>
				{#if bottomPadding}
					<div class="  pb-6"></div>
				{/if}
			{/key}
		</div>
	{/if}
</div>
