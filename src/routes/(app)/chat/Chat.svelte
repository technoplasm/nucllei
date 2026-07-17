<script lang="ts">
	import { v4 as uuidv4 } from 'uuid';
	import { toast } from 'svelte-sonner';
	import { PaneGroup, Pane, PaneResizer } from 'paneforge';

	import { getContext, onDestroy, onMount, tick } from 'svelte';
	const i18n: Writable<i18nType> = getContext('i18n');

	import { goto } from '$app/navigation';
	import { page } from '$app/stores';

	import { type Unsubscriber, type Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { WEBUI_BASE_URL } from '$lib/constants';

	import {
		chatId,
		chats,
		config,
		type Model,
		models,
		settings,
		showSidebar,
		WEBUI_NAME,
		user,
		showControls,
		showCallOverlay,
		currentChatPage,
		mobile,
		chatTitle
	} from '$lib/stores';
	import {
		convertMessagesToHistory,
		copyToClipboard,
		getMessageContentParts,
		createMessagesList,
		promptTemplate,
		getPromptVariables
	} from '$lib/utils';
	import { buildWireMessages, parseStopSequences } from '$lib/utils/chat-messages';
	import { applyCompletionData, applyMessageMutation } from '$lib/utils/chat-events';
	import {
		consumeCompletionStream,
		attachToTask,
		registerChatHandler,
		unregisterChatHandler,
		getActiveTaskIds,
		type CompletionEnvelope,
		type StreamContext
	} from '$lib/utils/completion-streams';

	import { createNewChat, getChatById, getChatList, updateChatById } from '$lib/apis/chats';
	import { generateOpenAIChatCompletion } from '$lib/apis/openai';
	import { getUserSettings } from '$lib/apis/users';
	import { chatCompleted, chatAction, stopTask, getTaskIdsByChatId } from '$lib/apis';

	import MessageInput from './MessageInput.svelte';
	import Messages from '$lib/components/chat/Messages.svelte';
	import Navbar from './Navbar.svelte';
	import ChatControls from './ChatControls.svelte';
	import EventConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Placeholder from './Placeholder.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	let { chatIdProp = '' }: { chatIdProp?: string } = $props();

	let loading = $state(false);

	const eventTarget = new EventTarget();
	let controlPane = $state<any>();
	let controlPaneComponent = $state<any>();

	let autoScroll = $state(true);
	let lastScrollTop = 0;
	let messagesContainerElement = $state<HTMLDivElement>();

	// llens: task 生成前(モデル接続待ち)の completion fetch を stop で中断するための
	// AbortController 群(複数モデル並列送信に対応して配列)。task 生成後の中断は
	// stopTask(サーバ側 cancel)が担う。
	let completionAbortControllers: AbortController[] = [];

	// 追従中はコンテンツの高さ変化そのものに張り付く(use: アクション)。
	// デルタ受信時の scrollToBottom だけだと、その後に非同期で高さが伸びる要素
	// (tool 結果の画像・チャート、コードハイライト等)の分が取り残される。
	const pinToBottomOnResize = (node: HTMLElement) => {
		const observer = new ResizeObserver(() => {
			if (autoScroll && messagesContainerElement) {
				messagesContainerElement.scrollTop = messagesContainerElement.scrollHeight;
			}
		});
		observer.observe(node);
		return {
			destroy: () => observer.disconnect()
		};
	};

	let showEventConfirmation = $state(false);
	let eventConfirmationTitle = $state('');
	let eventConfirmationMessage = $state('');
	let eventConfirmationInput = $state(false);
	let eventConfirmationInputPlaceholder = $state('');
	let eventConfirmationInputValue = $state('');
	let eventCallback: ((result: unknown) => Promise<void> | void) | null = null;

	let chatIdUnsubscriber: Unsubscriber | undefined;

	let selectedModels = $state(['']);
	let atSelectedModel = $state<Model | undefined>(undefined);
	const selectedModelIds = $derived(
		atSelectedModel !== undefined ? [atSelectedModel.id] : selectedModels
	);

	let chat = null;

	let history = $state<{ messages: Record<string, any>; currentId: string | null }>({
		messages: {},
		currentId: null
	});

	let taskIds = $state<string[] | null>(null);
	// wire v2: 再アタッチ済み(fetch 未解決含む)task の二重アタッチ防止
	const attachedTaskIds = new Set<string>();

	// Chat Input
	let prompt = $state('');
	let chatFiles = $state<any[]>([]);
	let files = $state<any[]>([]);
	let params = $state<any>({});

	// chatIdProp の追従。legacy `$:` と同じく初期値は init 時(onMount より先)に処理する
	// — loadChat 同期先頭の chatId.set() を onMount の !$chatId ガードが前提にしている。
	let loadedChatIdProp: string | null = null;
	const handleChatIdProp = () => {
		if (!chatIdProp || loadedChatIdProp === chatIdProp) {
			return;
		}
		loadedChatIdProp = chatIdProp;

		(async () => {
			loading = true;

			prompt = '';
			files = [];

			if (chatIdProp && (await loadChat())) {
				await tick();
				loading = false;

				if (localStorage.getItem(`chat-input-${chatIdProp}`)) {
					try {
						const input = JSON.parse(localStorage.getItem(`chat-input-${chatIdProp}`));

						prompt = input.prompt;
						files = input.files;
					} catch (e) {}
				}

				window.setTimeout(() => scrollToBottom(), 0);
				const chatInput = document.getElementById('chat-input');
				chatInput?.focus();
			} else {
				await goto('/');
			}
		})();
	};

	$effect(() => {
		if (selectedModels && chatIdProp !== '') {
			saveSessionSelectedModels();
		}
	});

	const saveSessionSelectedModels = () => {
		if (selectedModels.length === 0 || (selectedModels.length === 1 && selectedModels[0] === '')) {
			return;
		}
		sessionStorage.selectedModels = JSON.stringify(selectedModels);
	};

	const showMessage = async (message) => {
		const _chatId = $chatId;
		let _messageId = message.id;

		let messageChildrenIds = [];
		if (_messageId === null) {
			messageChildrenIds = Object.keys(history.messages).filter(
				(id) => history.messages[id].parentId === null
			);
		} else {
			messageChildrenIds = history.messages[_messageId].childrenIds;
		}

		while (messageChildrenIds.length !== 0) {
			_messageId = messageChildrenIds.at(-1);
			messageChildrenIds = history.messages[_messageId].childrenIds;
		}

		history.currentId = _messageId;

		await tick();
		await tick();
		await tick();

		const messageElement = document.getElementById(`message-${message.id}`);
		if (messageElement) {
			messageElement.scrollIntoView({ behavior: 'smooth' });
		}

		await tick();
		saveChatHandler(_chatId, history);
	};

	// wire v2: completion-streams(SSE)から届くイベント封筒を message へ適用する。
	// 表示中チャットのハンドラとして registerChatHandler で登録される。
	const chatEventHandler = async (envelope: CompletionEnvelope, context: StreamContext) => {
		const messageId = envelope.message_id;
		if (envelope.chat_id !== $chatId || !messageId) {
			return;
		}

		await tick();
		const message = history.messages[messageId];
		if (!message) {
			return;
		}

		const type = envelope?.data?.type ?? null;
		const data = envelope?.data?.data ?? null;
		// __event_call__(confirmation/input)への応答は call_id 付き封筒に対する
		// POST /api/tasks/{task_id}/calls/{call_id}(context.reply)で返す
		const callId = envelope.call_id;
		const reply = callId ? (result: unknown) => context.reply(callId, result) : null;

		await applyMessageEvent(type, data, message, envelope.chat_id, reply);
		history.messages[messageId] = message;
	};

	// completed / actions の応答 JSON に同梱される events(outlet filter / action の
	// emit — 封筒なしの {type, data} 形)も SSE と同じ適用関数を通す。
	const applyBufferedEvents = async (
		events: { type?: string; data?: any }[] | undefined,
		messageId: string,
		targetChatId: string
	) => {
		const message = history.messages[messageId];
		if (!message || !((events?.length ?? 0) > 0)) {
			return;
		}

		for (const event of events ?? []) {
			// バッファ応答経路に __event_call__ は無い(reply 不可)
			await applyMessageEvent(
				event?.type ?? null,
				event?.data ?? null,
				message,
				targetChatId,
				null
			);
		}
		history.messages[messageId] = message;
	};

	const applyMessageEvent = async (
		type: string | null,
		data: any,
		message: any,
		targetChatId: string,
		reply: ((result: unknown) => Promise<void>) | null
	) => {
		// message の状態だけを変えるイベント(status / task-cancelled / delta / files /
		// source 等)は純関数層(chat-events.ts)で適用する
		if (applyMessageMutation(type, data, message)) {
			return;
		}

		if (type === 'chat:completion') {
			await chatCompletionEventHandler(data, message, targetChatId);
		} else if (type === 'chat:title') {
			chatTitle.set(data);
			currentChatPage.set(1);
			await chats.set(await getChatList($currentChatPage));
		} else if (type === 'notification') {
			const toastType = data?.type ?? 'info';
			const toastContent = data?.content ?? '';

			if (toastType === 'success') {
				toast.success(toastContent);
			} else if (toastType === 'error') {
				toast.error(toastContent);
			} else if (toastType === 'warning') {
				toast.warning(toastContent);
			} else {
				toast.info(toastContent);
			}
		} else if (type === 'confirmation') {
			if (!reply) {
				console.warn('confirmation event without reply channel', data);
				return;
			}
			eventCallback = reply;

			eventConfirmationInput = false;
			showEventConfirmation = true;

			eventConfirmationTitle = data.title;
			eventConfirmationMessage = data.message;
		} else if (type === 'input') {
			if (!reply) {
				console.warn('input event without reply channel', data);
				return;
			}
			eventCallback = reply;

			eventConfirmationInput = true;
			showEventConfirmation = true;

			eventConfirmationTitle = data.title;
			eventConfirmationMessage = data.message;
			eventConfirmationInputPlaceholder = data.placeholder;
			eventConfirmationInputValue = data?.value ?? '';
		} else {
			console.warn('Unknown event type', type, data);
		}
	};

	const onMessageHandler = async (event: {
		origin: string;
		data: { type: string; text: string };
	}) => {
		if (event.origin !== window.origin) {
			return;
		}

		// Replace with your iframe's origin
		if (event.data.type === 'input:prompt') {
			console.debug(event.data.text);

			const inputElement = document.getElementById('chat-input');

			if (inputElement) {
				prompt = event.data.text;
				inputElement.focus();
			}
		}

		if (event.data.type === 'action:submit') {
			console.debug(event.data.text);

			if (prompt !== '') {
				await tick();
				submitPrompt(prompt);
			}
		}

		if (event.data.type === 'input:prompt:submit') {
			console.debug(event.data.text);

			if (event.data.text !== '') {
				await tick();
				submitPrompt(event.data.text);
			}
		}
	};

	// wire v2: 表示中チャットのイベントハンドラ登録($chatId 追従)。
	// 新規チャットは initChatHandler が chatId を確定させた時点で登録される
	// (consumeCompletionStream は登録ハンドラ、なければ +layout の fallback に配る)。
	let registeredChatId: string | null = null;
	const syncChatHandlerRegistration = (id: string) => {
		if (registeredChatId === id) {
			return;
		}
		if (registeredChatId) {
			unregisterChatHandler(registeredChatId, chatEventHandler);
			registeredChatId = null;
		}
		if (id) {
			registerChatHandler(id, chatEventHandler);
			registeredChatId = id;
		}
	};

	// コンポーネント初期化時点で購読を確立する(loadChat / 再アタッチは
	// onMount より先($: chatIdProp)に走り得るため)。解除は onDestroy。
	const handlerChatIdUnsubscriber: Unsubscriber = chatId.subscribe((value) => {
		syncChatHandlerRegistration(value);
	});

	let showControlsUnsubscriber: Unsubscriber | undefined;

	onMount(async () => {
		window.addEventListener('message', onMessageHandler);

		if (!$chatId) {
			chatIdUnsubscriber = chatId.subscribe(async (value) => {
				if (!value) {
					await tick(); // Wait for DOM updates
					await initNewChat();
				}
			});
		}

		if (localStorage.getItem(`chat-input-${chatIdProp}`)) {
			try {
				const input = JSON.parse(localStorage.getItem(`chat-input-${chatIdProp}`));
				prompt = input.prompt;
				files = input.files;
			} catch (e) {
				prompt = '';
				files = [];
			}
		}

		showControlsUnsubscriber = showControls.subscribe(async (value) => {
			if (controlPane && !$mobile) {
				try {
					if (value) {
						controlPaneComponent.openPane();
					} else {
						controlPane.collapse();
					}
				} catch (e) {
					// ignore
				}
			}

			if (!value) {
				showCallOverlay.set(false);
			}
		});

		const chatInput = document.getElementById('chat-input');
		chatInput?.focus();
	});

	onDestroy(() => {
		chatIdUnsubscriber?.();
		handlerChatIdUnsubscriber();
		showControlsUnsubscriber?.();
		window.removeEventListener('message', onMessageHandler);
		if (registeredChatId) {
			unregisterChatHandler(registeredChatId, chatEventHandler);
			registeredChatId = null;
		}
	});

	//////////////////////////
	// Web functions
	//////////////////////////

	const initNewChat = async () => {
		if ($page.url.searchParams.get('models')) {
			selectedModels = $page.url.searchParams.get('models')?.split(',');
		} else if ($page.url.searchParams.get('model')) {
			const urlModels = $page.url.searchParams.get('model')?.split(',');

			if (urlModels.length === 1) {
				const m = $models.find((m) => m.id === urlModels[0]);
				if (!m) {
					const modelSelectorButton = document.getElementById('model-selector-0-button');
					if (modelSelectorButton) {
						modelSelectorButton.click();
						await tick();

						const modelSelectorInput = document.getElementById('model-search-input');
						if (modelSelectorInput) {
							modelSelectorInput.focus();
							modelSelectorInput.value = urlModels[0];
							modelSelectorInput.dispatchEvent(new Event('input'));
						}
					}
				} else {
					selectedModels = urlModels;
				}
			} else {
				selectedModels = urlModels;
			}
		} else {
			if (sessionStorage.selectedModels) {
				selectedModels = JSON.parse(sessionStorage.selectedModels);
				sessionStorage.removeItem('selectedModels');
			} else {
				if ($settings?.models) {
					selectedModels = $settings?.models;
				} else if ($config?.default_models) {
					selectedModels = $config?.default_models.split(',');
				}
			}
		}

		selectedModels = selectedModels.filter((modelId) => $models.map((m) => m.id).includes(modelId));
		if (selectedModels.length === 0 || (selectedModels.length === 1 && selectedModels[0] === '')) {
			if ($models.length > 0) {
				selectedModels = [$models[0].id];
			} else {
				selectedModels = [''];
			}
		}

		await showControls.set(false);
		await showCallOverlay.set(false);

		if ($page.url.pathname.includes('/c/')) {
			window.history.replaceState(history.state, '', `/`);
		}

		autoScroll = true;

		await chatId.set('');
		await chatTitle.set('');

		history = {
			messages: {},
			currentId: null
		};

		chatFiles = [];
		params = {};

		if ($page.url.searchParams.get('call') === 'true') {
			showCallOverlay.set(true);
			showControls.set(true);
		}

		if ($page.url.searchParams.get('q')) {
			prompt = $page.url.searchParams.get('q') ?? '';

			if (prompt) {
				await tick();
				submitPrompt(prompt);
			}
		}

		selectedModels = selectedModels.map((modelId) =>
			$models.map((m) => m.id).includes(modelId) ? modelId : ''
		);

		const userSettings = await getUserSettings();

		if (userSettings) {
			settings.set(userSettings.ui);
		} else {
			settings.set(JSON.parse(localStorage.getItem('settings') ?? '{}'));
		}

		const chatInput = document.getElementById('chat-input');
		setTimeout(() => chatInput?.focus(), 0);
	};

	const loadChat = async () => {
		chatId.set(chatIdProp);
		chat = await getChatById($chatId).catch(async (error) => {
			await goto('/');
			return null;
		});

		if (chat) {
			const chatContent = chat.chat;

			if (chatContent) {
				selectedModels =
					(chatContent?.models ?? undefined) !== undefined
						? chatContent.models
						: [chatContent.models ?? ''];
				history =
					(chatContent?.history ?? undefined) !== undefined
						? chatContent.history
						: convertMessagesToHistory(chatContent.messages);

				chatTitle.set(chatContent.title);

				const userSettings = await getUserSettings();

				if (userSettings) {
					await settings.set(userSettings.ui);
				} else {
					await settings.set(JSON.parse(localStorage.getItem('settings') ?? '{}'));
				}

				params = chatContent?.params ?? {};
				chatFiles = chatContent?.files ?? [];

				autoScroll = true;
				await tick();

				const taskRes = await getTaskIdsByChatId($chatId).catch((error) => {
					return null;
				});

				if (taskRes) {
					taskIds = taskRes.task_ids;

					// wire v2: 走行中タスクへ再アタッチ(SSE)。このモジュールが元 POST の
					// ストリームを消費中(ページ内遷移で戻ってきた等)なら重複購読しない。
					// attachedTaskIds は「アタッチ fetch が未解決の間」の二重発火ガード
					// (getActiveTaskIds は接続確立後にしか現れない)。
					const activeTaskIds = getActiveTaskIds($chatId);
					for (const taskId of taskIds ?? []) {
						if (!activeTaskIds.includes(taskId) && !attachedTaskIds.has(taskId)) {
							attachedTaskIds.add(taskId);
							attachToTask($chatId, taskId).catch((error) => {
								console.error('task reattach failed', taskId, error);
							});
						}
					}
				}

				// llens: 生きた task が無ければ、未完の assistant メッセージは「中断された
				// 応答」として done 化しスピナーを止める(本文ゼロなら ResponseMessage が
				// 中断メッセージを表示する)。task が生きている場合はストリームが SSE
				// 経由で続いているので触らない — ぐるぐるは「task が生きている間だけ」。
				if (history.currentId && (taskRes?.task_ids ?? []).length === 0) {
					for (const message of Object.values(history.messages)) {
						if (message.role === 'assistant') {
							message.done = true;
						}
					}
				}

				await tick();

				return true;
			} else {
				return null;
			}
		}
	};

	const scrollToBottom = async () => {
		await tick();
		if (messagesContainerElement) {
			messagesContainerElement.scrollTop = messagesContainerElement.scrollHeight;
		}
	};
	const chatCompletedHandler = async (targetChatId, modelId, responseMessageId, messages) => {
		const res = await chatCompleted({
			model: modelId,
			messages: messages.map((m) => ({
				id: m.id,
				role: m.role,
				content: m.content,
				info: m.info ? m.info : undefined,
				timestamp: m.timestamp,
				...(m.usage ? { usage: m.usage } : {}),
				...(m.sources ? { sources: m.sources } : {}),
				// llens: outlet filter が turn の成否を判別できるようにする
				// (error = 失敗、interrupted = ユーザー中断。どちらも無ければ成功)
				...(m.error ? { error: m.error } : {}),
				...(m.interrupted ? { interrupted: true } : {})
			})),
			model_item: $models.find((m) => m.id === modelId),
			chat_id: targetChatId,
			id: responseMessageId
		}).catch((error) => {
			toast.error(`${error}`);
			messages.at(-1).error = { content: error };

			return null;
		});

		if (res !== null && res.messages) {
			// Update chat history with the new messages
			for (const message of res.messages) {
				if (message?.id) {
					// Add null check for message and message.id
					history.messages[message.id] = {
						...history.messages[message.id],
						...(history.messages[message.id].content !== message.content
							? { originalContent: history.messages[message.id].content }
							: {}),
						...message
					};
				}
			}
		}

		// wire v2: outlet filter が emit したイベント(status 等)は応答 JSON の
		// events に同梱される — SSE と同じ適用関数で処理する
		if (res !== null) {
			await applyBufferedEvents(res.events, responseMessageId, targetChatId);
		}

		await tick();

		if ($chatId == targetChatId) {
			chat = await updateChatById(targetChatId, {
				models: selectedModels,
				messages: messages,
				history: history,
				params: params,
				files: chatFiles
			});

			currentChatPage.set(1);
			await chats.set(await getChatList($currentChatPage));
		}

		taskIds = null;
	};

	const chatActionHandler = async (
		targetChatId,
		actionId,
		modelId,
		responseMessageId,
		event = null
	) => {
		const messages = createMessagesList(history, responseMessageId);

		const res = await chatAction(actionId, {
			model: modelId,
			messages: messages.map((m) => ({
				id: m.id,
				role: m.role,
				content: m.content,
				info: m.info ? m.info : undefined,
				timestamp: m.timestamp,
				...(m.sources ? { sources: m.sources } : {})
			})),
			...(event ? { event: event } : {}),
			model_item: $models.find((m) => m.id === modelId),
			chat_id: targetChatId,
			id: responseMessageId
		}).catch((error) => {
			toast.error(`${error}`);
			messages.at(-1).error = { content: error };
			return null;
		});

		if (res !== null && res.messages) {
			// Update chat history with the new messages
			for (const message of res.messages) {
				history.messages[message.id] = {
					...history.messages[message.id],
					...(history.messages[message.id].content !== message.content
						? { originalContent: history.messages[message.id].content }
						: {}),
					...message
				};
			}
		}

		// wire v2: action / outlet filter の emit は応答 JSON の events に同梱される
		if (res !== null) {
			await applyBufferedEvents(res.events, responseMessageId, targetChatId);
		}

		if ($chatId == targetChatId) {
			chat = await updateChatById(targetChatId, {
				models: selectedModels,
				messages: messages,
				history: history,
				params: params,
				files: chatFiles
			});

			currentChatPage.set(1);
			await chats.set(await getChatList($currentChatPage));
		}
	};

	const createMessagePair = async (userPrompt) => {
		prompt = '';
		if (selectedModels.length === 0) {
			toast.error($i18n.t('Model not selected'));
		} else {
			const modelId = selectedModels[0];
			const model = $models.filter((m) => m.id === modelId).at(0);

			const messages = createMessagesList(history, history.currentId);
			const parentMessage = messages.length !== 0 ? messages.at(-1) : null;

			const userMessageId = uuidv4();
			const responseMessageId = uuidv4();

			const userMessage = {
				id: userMessageId,
				parentId: parentMessage ? parentMessage.id : null,
				childrenIds: [responseMessageId],
				role: 'user',
				content: userPrompt ? userPrompt : `[PROMPT] ${userMessageId}`,
				timestamp: Math.floor(Date.now() / 1000)
			};

			const responseMessage = {
				id: responseMessageId,
				parentId: userMessageId,
				childrenIds: [],
				role: 'assistant',
				content: `[RESPONSE] ${responseMessageId}`,
				done: true,

				model: modelId,
				modelName: model.name ?? model.id,
				modelIdx: 0,
				timestamp: Math.floor(Date.now() / 1000)
			};

			if (parentMessage) {
				parentMessage.childrenIds.push(userMessageId);
				history.messages[parentMessage.id] = parentMessage;
			}
			history.messages[userMessageId] = userMessage;
			history.messages[responseMessageId] = responseMessage;

			history.currentId = responseMessageId;

			await tick();

			if (autoScroll) {
				scrollToBottom();
			}

			if (messages.length === 0) {
				await initChatHandler(history);
			} else {
				await saveChatHandler($chatId, history);
			}
		}
	};

	const addMessages = async ({ modelId, parentId, messages }) => {
		const model = $models.filter((m) => m.id === modelId).at(0);

		let parentMessage = history.messages[parentId];
		let currentParentId = parentMessage ? parentMessage.id : null;
		for (const message of messages) {
			let messageId = uuidv4();

			if (message.role === 'user') {
				const userMessage = {
					id: messageId,
					parentId: currentParentId,
					childrenIds: [],
					timestamp: Math.floor(Date.now() / 1000),
					...message
				};

				if (parentMessage) {
					parentMessage.childrenIds.push(messageId);
					history.messages[parentMessage.id] = parentMessage;
				}

				history.messages[messageId] = userMessage;
				parentMessage = userMessage;
				currentParentId = messageId;
			} else {
				const responseMessage = {
					id: messageId,
					parentId: currentParentId,
					childrenIds: [],
					done: true,
					model: model.id,
					modelName: model.name ?? model.id,
					modelIdx: 0,
					timestamp: Math.floor(Date.now() / 1000),
					...message
				};

				if (parentMessage) {
					parentMessage.childrenIds.push(messageId);
					history.messages[parentMessage.id] = parentMessage;
				}

				history.messages[messageId] = responseMessage;
				parentMessage = responseMessage;
				currentParentId = messageId;
			}
		}

		history.currentId = currentParentId;
		await tick();

		if (autoScroll) {
			scrollToBottom();
		}

		if (messages.length === 0) {
			await initChatHandler(history);
		} else {
			await saveChatHandler($chatId, history);
		}
	};

	// wire v2: chat:completion の data(スナップショット / delta / done / error / usage /
	// sources)の message への適用は純関数 applyCompletionData に一元化。ここは
	// 戻りフラグで副作用(TTS・haptic・自動スクロール・done 時の completed 経路)を駆動する。
	const chatCompletionEventHandler = async (data, message, chatId) => {
		const { contentChanged, done, error } = applyCompletionData(message, data);

		if (error) {
			// {error: {content}} の一形 — message.error は適用済み。ここでは通知のみ
			toast.error(message.error?.content ?? '');
		}

		if (contentChanged) {
			if (navigator.vibrate && ($settings?.hapticFeedback ?? false)) {
				navigator.vibrate(5);
			}

			// Emit chat event for TTS
			const messageContentParts = getMessageContentParts(
				message.content,
				$config?.audio?.tts?.split_on ?? 'punctuation'
			);
			messageContentParts.pop();

			// dispatch only last sentence and make sure it hasn't been dispatched before
			if (messageContentParts.length > 0 && messageContentParts.at(-1) !== message.lastSentence) {
				message.lastSentence = messageContentParts.at(-1);
				eventTarget.dispatchEvent(
					new CustomEvent('chat', {
						detail: {
							id: message.id,
							content: messageContentParts.at(-1)
						}
					})
				);
			}
		}

		history.messages[message.id] = message;

		if (done) {
			if ($settings.responseAutoCopy) {
				copyToClipboard(message.content);
			}

			if ($settings.responseAutoPlayback && !$showCallOverlay) {
				await tick();
				document.getElementById(`speak-button-${message.id}`)?.click();
			}

			// Emit chat event for TTS
			const lastMessageContentPart =
				getMessageContentParts(message.content, $config?.audio?.tts?.split_on ?? 'punctuation')?.at(
					-1
				) ?? '';
			if (lastMessageContentPart) {
				eventTarget.dispatchEvent(
					new CustomEvent('chat', {
						detail: { id: message.id, content: lastMessageContentPart }
					})
				);
			}
			eventTarget.dispatchEvent(
				new CustomEvent('chat:finish', {
					detail: {
						id: message.id,
						content: message.content
					}
				})
			);

			history.messages[message.id] = message;
			await chatCompletedHandler(
				chatId,
				message.model,
				message.id,
				createMessagesList(history, message.id)
			);
		}

		if (autoScroll) {
			scrollToBottom();
		}
	};

	//////////////////////////
	// Chat functions
	//////////////////////////

	const submitPrompt = async (userPrompt, { _raw = false } = {}) => {
		const messages = createMessagesList(history, history.currentId);
		const _selectedModels = selectedModels.map((modelId) =>
			$models.map((m) => m.id).includes(modelId) ? modelId : ''
		);
		if (JSON.stringify(selectedModels) !== JSON.stringify(_selectedModels)) {
			selectedModels = _selectedModels;
		}

		if (userPrompt === '' && files.length === 0) {
			toast.error($i18n.t('Please enter a prompt'));
			return;
		}
		if (selectedModels.includes('')) {
			toast.error($i18n.t('Model not selected'));
			return;
		}

		if (messages.length != 0 && messages.at(-1).done != true) {
			// Response not done
			return;
		}
		if (messages.length != 0 && messages.at(-1).error && !messages.at(-1).content) {
			// Error in response
			toast.error($i18n.t(`Oops! There was an error in the previous response.`));
			return;
		}
		// llens(#69): 画像も file 化しアップロード経路に乗ったため、type を問わず uploading を待つ
		if (files.length > 0 && files.filter((file) => file.status === 'uploading').length > 0) {
			toast.error(
				$i18n.t(`Oops! There are files still uploading. Please wait for the upload to complete.`)
			);
			return;
		}
		if (
			($config?.file?.max_count ?? null) !== null &&
			files.length + chatFiles.length > $config?.file?.max_count
		) {
			toast.error(
				$i18n.t(`You can only chat with a maximum of {{maxCount}} file(s) at a time.`, {
					maxCount: $config?.file?.max_count
				})
			);
			return;
		}

		prompt = '';

		// Reset chat input textarea
		const chatInputElement = document.getElementById('chat-input');

		if (chatInputElement) {
			await tick();
			chatInputElement.style.height = '';
		}

		const _files = JSON.parse(JSON.stringify(files));
		chatFiles.push(..._files.filter((item) => ['file'].includes(item.type)));
		chatFiles = chatFiles.filter(
			// Remove duplicates — id を持つエントリは file.id で一意化(#69: 再添付は
			// メタ差分で JSON 全体比較をすり抜け、全文 context が二重注入される)。
			// id なし(旧形式)は従来の JSON 比較にフォールバック。
			(item, index, array) =>
				array.findIndex((i) =>
					item.id ? i.id === item.id : JSON.stringify(i) === JSON.stringify(item)
				) === index
		);

		files = [];
		prompt = '';

		// Create user message
		let userMessageId = uuidv4();
		let userMessage = {
			id: userMessageId,
			parentId: messages.length !== 0 ? messages.at(-1).id : null,
			childrenIds: [],
			role: 'user',
			content: userPrompt,
			files: _files.length > 0 ? _files : undefined,
			timestamp: Math.floor(Date.now() / 1000), // Unix epoch
			models: selectedModels
		};

		// Add message to history and Set currentId to messageId
		history.messages[userMessageId] = userMessage;
		history.currentId = userMessageId;

		// Append messageId to childrenIds of parent message
		if (messages.length !== 0) {
			history.messages[messages.at(-1).id].childrenIds.push(userMessageId);
		}

		// focus on chat input
		const chatInput = document.getElementById('chat-input');
		chatInput?.focus();

		saveSessionSelectedModels();

		await sendPrompt(history, userPrompt, userMessageId, { newChat: true });
	};

	const sendPrompt = async (
		_history,
		prompt: string,
		parentId: string,
		{ modelId = null, modelIdx = null, newChat = false } = {}
	) => {
		if (autoScroll) {
			scrollToBottom();
		}

		let _chatId = $chatId;
		_history = JSON.parse(JSON.stringify(_history));

		const responseMessageIds: Record<PropertyKey, string> = {};
		// If modelId is provided, use it, else use selected model
		const targetModelIds = modelId ? [modelId] : selectedModelIds;

		// Create response messages for each selected model
		for (const [_modelIdx, modelId] of targetModelIds.entries()) {
			const model = $models.filter((m) => m.id === modelId).at(0);

			if (model) {
				let responseMessageId = uuidv4();
				let responseMessage = {
					parentId: parentId,
					id: responseMessageId,
					childrenIds: [],
					role: 'assistant',
					content: '',
					model: model.id,
					modelName: model.name ?? model.id,
					modelIdx: modelIdx ? modelIdx : _modelIdx,
					userContext: null,
					timestamp: Math.floor(Date.now() / 1000) // Unix epoch
				};

				// Add message to history and Set currentId to messageId
				history.messages[responseMessageId] = responseMessage;
				history.currentId = responseMessageId;

				// Append messageId to childrenIds of parent message
				if (parentId !== null && history.messages[parentId]) {
					// Add null check before accessing childrenIds
					history.messages[parentId].childrenIds = [
						...history.messages[parentId].childrenIds,
						responseMessageId
					];
				}

				responseMessageIds[`${modelId}-${modelIdx ? modelIdx : _modelIdx}`] = responseMessageId;
			}
		}
		history = history;

		// Create new chat if newChat is true and first user message
		if (newChat && _history.messages[_history.currentId].parentId === null) {
			_chatId = await initChatHandler(_history);
		}

		await tick();

		_history = JSON.parse(JSON.stringify(history));
		// Save chat after all messages have been created
		await saveChatHandler(_chatId, _history);

		await Promise.all(
			targetModelIds.map(async (modelId, _modelIdx) => {
				const model = $models.filter((m) => m.id === modelId).at(0);

				if (model) {
					const messages = createMessagesList(_history, parentId);
					// If there are image files, check if model is vision capable
					const hasImages = messages.some((message) =>
						message.files?.some((file) => file.type === 'image')
					);

					if (hasImages && !(model.info?.meta?.capabilities?.vision ?? true)) {
						toast.error(
							$i18n.t('Model {{modelName}} is not vision capable', {
								modelName: model.name ?? model.id
							})
						);
					}

					let responseMessageId =
						responseMessageIds[`${modelId}-${modelIdx ? modelIdx : _modelIdx}`];
					let responseMessage = _history.messages[responseMessageId];

					// llens(#63): 個人化(Memory)は機能ごと廃止(テーブルは温存)。
					// userContext 注入は行わない。
					let userContext = null;
					responseMessage.userContext = userContext;

					scrollToBottom();
					await sendChatCompletionRequest(_history, model, responseMessageId, _chatId);
				} else {
					toast.error($i18n.t(`Model {{modelId}} not found`, { modelId }));
				}
			})
		);

		currentChatPage.set(1);
		chats.set(await getChatList($currentChatPage));
	};

	// wire v2: POST /api/chat/completions の応答は SSE。ここでは送信と消費開始までを
	// 担い、イベントの適用は registerChatHandler 済みの chatEventHandler が行う。
	const sendChatCompletionRequest = async (_history, model, responseMessageId, _chatId) => {
		const responseMessage = _history.messages[responseMessageId];
		const userMessage = _history.messages[responseMessage.parentId];

		let files = JSON.parse(JSON.stringify(chatFiles));
		files.push(...(userMessage?.files ?? []).filter((item) => ['file'].includes(item.type)));
		// Remove duplicates
		files = files.filter(
			(item, index, array) =>
				array.findIndex((i) => JSON.stringify(i) === JSON.stringify(item)) === index
		);

		scrollToBottom();
		eventTarget.dispatchEvent(
			new CustomEvent('chat:start', {
				detail: {
					id: responseMessageId
				}
			})
		);
		await tick();

		const stream =
			model?.info?.params?.stream_response ??
			$settings?.params?.stream_response ??
			params?.stream_response ??
			true;

		// wire 形への組み立て(system 注入・画像展開・tool 往復復元)は純関数層
		// (chat-messages.ts buildWireMessages)— vitest で契約を固定している
		const messages = buildWireMessages(
			createMessagesList(_history, responseMessageId),
			params?.system ? promptTemplate(params?.system ?? '', $user?.name, undefined) : null
		);

		const abortController = new AbortController();
		completionAbortControllers.push(abortController);

		const res = await generateOpenAIChatCompletion(
			{
				stream: stream,
				model: model.id,
				messages: messages,
				params: {
					...$settings?.params,
					...params,

					format: $settings.requestFormat ?? undefined,
					keep_alive: $settings.keepAlive ?? undefined,
					stop: parseStopSequences(params?.stop ?? $settings?.params?.stop)
				},

				files: (files?.length ?? 0) > 0 ? files : undefined,
				// llens(#54): tool はモデル割り当て一本 — tool_ids はサーバー側解決に移行し送らない
				// llens(#70): features(image_generation)はネイティブ画像生成の BE 撤去に伴い送らない

				variables: {
					...getPromptVariables($user?.name, undefined)
				},
				model_item: $models.find((m) => m.id === model.id),

				chat_id: $chatId,
				id: responseMessageId,

				...((messages.length == 1 ||
					(messages.length == 2 &&
						messages.at(0)?.role === 'system' &&
						messages.at(1)?.role === 'user')) &&
				(selectedModels[0] === model.id || atSelectedModel !== undefined)
					? {
							background_tasks: {
								title_generation: $settings?.title?.auto ?? true
							}
						}
					: {})
				// llens(#57/#70): stream_options.include_usage の注入は backend
				// (middleware の is_usage_requested — 既定 ON・capabilities.usage=False で opt-out)
				// に一元化。クライアント側の条件付き付与は撤去した。
			},
			`${WEBUI_BASE_URL}/api`,
			abortController.signal
		).catch(async (error) => {
			if (abortController.signal.aborted) {
				// stop によるユーザー中断 — エラーにしない(task-cancelled と同じ扱い)。
				// completed(outlet)へは下の !res 分岐から interrupted 付きで流れる
				responseMessage.done = true;
				responseMessage.interrupted = true;
			} else {
				// HTTP 応答エラー(payload 処理失敗 = 400 等)/ 接続エラー
				handleCompletionRequestError(error, responseMessage);
			}

			history.messages[responseMessageId] = responseMessage;
			history.currentId = responseMessageId;
			return null;
		});

		completionAbortControllers = completionAbortControllers.filter((c) => c !== abortController);

		if (res && res.body) {
			// wire v2: SSE を EOF まで消費する(await しない — fire-and-consume)。
			// イベントの適用は登録ハンドラ(Chat が unmount 済みなら +layout の
			// fallback)が担う。EOF がタスク終端(done の後にも chat:title が来る)。
			consumeCompletionStream(_chatId, res.body, {
				onTask: (taskId) => {
					taskIds = [...(taskIds ?? []), taskId];
				}
			}).catch((error) => {
				// 消費中の接続断。タスクはサーバー側で継続する(リロード/再訪で再アタッチ)
				console.error('completion stream disconnected', error);
				responseMessage.error = { content: `${error}` };
				history.messages[responseMessageId] = responseMessage;
			});
		} else {
			// llens: task 生成前の失敗(接続エラー等)は done イベントが来ないため、
			// ここから completed(outlet filter)を通す — error 付き message が
			// outlet に渡り、turn の失敗として記録できる
			await chatCompletedHandler(
				_chatId,
				model.id,
				responseMessageId,
				createMessagesList(history, responseMessageId)
			);
		}

		await tick();
		scrollToBottom();
	};

	// HTTP 応答エラー用(fetch catch / res.json 形)。イベント内エラーは
	// {error: {content}} の一形で chatCompletionEventHandler 側が処理する。
	const handleCompletionRequestError = (error: any, responseMessage: any) => {
		console.error(error);
		const errorMessage = error?.detail ?? error?.error?.message ?? error?.message ?? `${error}`;
		toast.error(errorMessage);

		responseMessage.error = {
			content: $i18n.t(`Uh-oh! There was an issue with the response.`) + '\n' + errorMessage
		};
		responseMessage.done = true;

		history.messages[responseMessage.id] = responseMessage;
	};

	const stopResponse = async () => {
		// llens: task 生成前(モデル接続待ち)の fetch を先に落とす。これが無いと
		// stop 後に上流が応答した時点で task が生まれ、「中断済み」のはずのチャットへ
		// 応答が流れ込む
		for (const controller of completionAbortControllers) {
			controller.abort();
		}
		completionAbortControllers = [];

		// wire v2: 消費中ストリームの task も含めて止める(サーバー側 cancel)。
		// SSE 自体は task-cancelled → done → EOF の順で自然に閉じる
		const stopTaskIds = [...new Set([...(taskIds ?? []), ...getActiveTaskIds($chatId)])];

		if (stopTaskIds.length > 0) {
			for (const taskId of stopTaskIds) {
				await stopTask(taskId).catch((error) => {
					toast.error(`${error}`);
					return null;
				});
			}

			taskIds = null;

			const responseMessage = history.messages[history.currentId];
			// Set all response messages to done
			for (const messageId of history.messages[responseMessage.parentId].childrenIds) {
				history.messages[messageId].done = true;
			}

			history.messages[history.currentId] = responseMessage;

			if (autoScroll) {
				scrollToBottom();
			}
		}
	};

	const submitMessage = async (parentId, prompt) => {
		let userPrompt = prompt;
		let userMessageId = uuidv4();

		let userMessage = {
			id: userMessageId,
			parentId: parentId,
			childrenIds: [],
			role: 'user',
			content: userPrompt,
			models: selectedModels
		};

		if (parentId !== null) {
			history.messages[parentId].childrenIds = [
				...history.messages[parentId].childrenIds,
				userMessageId
			];
		}

		history.messages[userMessageId] = userMessage;
		history.currentId = userMessageId;

		await tick();

		if (autoScroll) {
			scrollToBottom();
		}

		await sendPrompt(history, userPrompt, userMessageId);
	};

	const regenerateResponse = async (message) => {
		if (history.currentId) {
			let userMessage = history.messages[message.parentId];
			let userPrompt = userMessage.content;

			if (autoScroll) {
				scrollToBottom();
			}

			if ((userMessage?.models ?? [...selectedModels]).length == 1) {
				// If user message has only one model selected, sendPrompt automatically selects it for regeneration
				await sendPrompt(history, userPrompt, userMessage.id);
			} else {
				// If there are multiple models selected, use the model of the response message for regeneration
				// e.g. many model chat
				await sendPrompt(history, userPrompt, userMessage.id, {
					modelId: message.model,
					modelIdx: message.modelIdx
				});
			}
		}
	};

	const continueResponse = async () => {
		const _chatId = $chatId;

		if (history.currentId && history.messages[history.currentId].done == true) {
			const responseMessage = history.messages[history.currentId];
			responseMessage.done = false;
			await tick();

			const model = $models
				.filter((m) => m.id === (responseMessage?.selectedModelId ?? responseMessage.model))
				.at(0);

			if (model) {
				await sendChatCompletionRequest(history, model, responseMessage.id, _chatId);
			}
		}
	};

	const initChatHandler = async (history) => {
		let _chatId = $chatId;

		chat = await createNewChat({
			id: _chatId,
			title: $i18n.t('New Chat'),
			models: selectedModels,
			system: undefined,
			params: params,
			history: history,
			messages: createMessagesList(history, history.currentId),
			timestamp: Date.now()
		});

		_chatId = chat.id;
		await chatId.set(_chatId);

		await chats.set(await getChatList($currentChatPage));
		currentChatPage.set(1);

		window.history.replaceState(history.state, '', `/c/${_chatId}`);
		await tick();

		return _chatId;
	};

	const saveChatHandler = async (_chatId, history) => {
		if ($chatId == _chatId) {
			chat = await updateChatById(_chatId, {
				models: selectedModels,
				history: history,
				messages: createMessagesList(history, history.currentId),
				params: params,
				files: chatFiles
			});
			currentChatPage.set(1);
			await chats.set(await getChatList($currentChatPage));
		}
	};

	// 初期 chatIdProp は init 時(onMount より先)に処理する。legacy `$:` は全宣言の
	// 評価後に走っていたため、呼び出しはスクリプト末尾に置く(loadChat の TDZ 回避)。
	handleChatIdProp();
	$effect(() => {
		void chatIdProp;
		handleChatIdProp();
	});
</script>

<svelte:head>
	<title>
		{$chatTitle
			? `${$chatTitle.length > 30 ? `${$chatTitle.slice(0, 30)}...` : $chatTitle} | ${$WEBUI_NAME}`
			: `${$WEBUI_NAME}`}
	</title>
</svelte:head>

<audio id="audioElement" src="" style="display: none;"></audio>

<EventConfirmDialog
	bind:show={showEventConfirmation}
	title={eventConfirmationTitle}
	message={eventConfirmationMessage}
	input={eventConfirmationInput}
	inputPlaceholder={eventConfirmationInputPlaceholder}
	inputValue={eventConfirmationInputValue}
	onConfirm={(inputValue) => {
		if (inputValue) {
			eventCallback(inputValue);
		} else {
			eventCallback(true);
		}
	}}
	onCancel={() => {
		eventCallback(false);
	}}
/>

<div
	class="h-screen max-h-[100dvh] transition-width duration-200 ease-in-out {$showSidebar
		? '  md:max-w-[calc(100%-var(--sidebar-width))]'
		: ' '} w-full max-w-full flex flex-col"
	id="chat-container"
>
	{#if !loading}
		{#if $settings?.backgroundImageUrl ?? null}
			<div
				class="absolute {$showSidebar
					? 'md:max-w-[calc(100%-var(--sidebar-width))] md:translate-x-[var(--sidebar-width)]'
					: ''} top-0 left-0 w-full h-full bg-cover bg-center bg-no-repeat"
				style="background-image: url({$settings.backgroundImageUrl})  "
			></div>

			<div
				class="absolute top-0 left-0 w-full h-full bg-linear-to-t from-white to-white/85 dark:from-gray-900 dark:to-gray-900/90 z-0"
			></div>
		{/if}

		<PaneGroup direction="horizontal" class="w-full h-full">
			<Pane defaultSize={50} class="h-full flex relative max-w-full flex-col">
				<Navbar
					chat={{
						id: $chatId,
						chat: {
							title: $chatTitle,
							models: selectedModels,
							system: undefined,
							params: params,
							history: history,
							timestamp: Date.now()
						}
					}}
					{history}
					bind:selectedModels
					shareEnabled={!!history.currentId}
					{initNewChat}
				/>

				<div class="flex flex-col flex-auto z-10 w-full @container">
					{#if $settings?.landingPageMode === 'chat' || createMessagesList(history, history.currentId).length > 0}
						<div
							class=" pb-2.5 flex flex-col justify-between w-full flex-auto overflow-auto h-0 max-w-full z-10 scrollbar-hidden"
							id="messages-container"
							bind:this={messagesContainerElement}
							onscroll={() => {
								// 追従の on/off は方向ベースで判定する。「最下部±Npx か」の再計算だけだと、
								// ストリーム中に scrollToBottom → scroll イベント処理前に次のデルタで
								// 高さが伸びる競合で追従が勝手に切れる(text 出力で追従しない不具合)。
								// 下端付近に来たら ON / 上方向スクロール(ユーザーの離脱)だけ OFF。
								const el = messagesContainerElement;
								if (el.scrollHeight - el.scrollTop <= el.clientHeight + 50) {
									autoScroll = true;
								} else if (el.scrollTop < lastScrollTop) {
									autoScroll = false;
								}
								lastScrollTop = el.scrollTop;
							}}
						>
							<div class=" h-full w-full flex flex-col" use:pinToBottomOnResize>
								<Messages
									chatId={$chatId}
									bind:history
									bind:autoScroll
									bind:prompt
									{selectedModels}
									{atSelectedModel}
									{sendPrompt}
									{showMessage}
									{submitMessage}
									{continueResponse}
									{regenerateResponse}
									{chatActionHandler}
									{addMessages}
									bottomPadding={files.length > 0}
								/>
							</div>
						</div>

						<div class=" pb-[1rem]">
							<MessageInput
								{history}
								{taskIds}
								{selectedModels}
								bind:files
								bind:prompt
								bind:autoScroll
								bind:atSelectedModel
								transparentBackground={$settings?.backgroundImageUrl ?? false}
								{stopResponse}
								{createMessagePair}
								onChange={(input) => {
									if (input.prompt) {
										localStorage.setItem(`chat-input-${$chatId}`, JSON.stringify(input));
									} else {
										localStorage.removeItem(`chat-input-${$chatId}`);
									}
								}}
								onSubmit={async (userPrompt) => {
									if (userPrompt || files.length > 0) {
										await tick();
										submitPrompt(userPrompt);
									}
								}}
							/>

							<div
								class="absolute bottom-1 text-xs text-content-muted text-center line-clamp-1 right-0 left-0"
							>
								<!-- {$i18n.t('LLMs can make mistakes. Verify important information.')} -->
							</div>
						</div>
					{:else}
						<div class="overflow-auto w-full h-full flex items-center">
							<Placeholder
								{history}
								{selectedModels}
								bind:files
								bind:prompt
								bind:autoScroll
								bind:atSelectedModel
								transparentBackground={$settings?.backgroundImageUrl ?? false}
								{stopResponse}
								{createMessagePair}
								onSubmit={async (userPrompt) => {
									if (userPrompt || files.length > 0) {
										await tick();
										submitPrompt(userPrompt);
									}
								}}
							/>
						</div>
					{/if}
				</div>
			</Pane>

			<ChatControls
				bind:this={controlPaneComponent}
				bind:history
				bind:chatFiles
				bind:files
				bind:pane={controlPane}
				chatId={$chatId}
				modelId={selectedModelIds?.at(0) ?? null}
				{submitPrompt}
				{stopResponse}
				{eventTarget}
			/>
		</PaneGroup>
	{:else}
		<div class=" flex items-center justify-center h-full w-full">
			<div class="m-auto">
				<Spinner />
			</div>
		</div>
	{/if}
</div>
