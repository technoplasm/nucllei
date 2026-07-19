<script lang="ts">
	import { PaneGroup, Pane } from 'paneforge';

	import { getContext, onDestroy, onMount, tick } from 'svelte';
	const i18n: Writable<i18nType> = getContext('i18n');

	import { goto } from '$app/navigation';
	import { page } from '$app/stores';

	import { type Unsubscriber, type Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import {
		chatId,
		config,
		models,
		settings,
		showSidebar,
		WEBUI_NAME,
		showControls,
		showCallOverlay,
		mobile,
		chatTitle
	} from '$lib/stores';
	import { createMessagesList } from '$lib/utils';
	import {
		attachToTask,
		registerChatHandler,
		unregisterChatHandler,
		getActiveTaskIds
	} from '$lib/utils/completion-streams';

	import { getChatById } from '$lib/apis/chats';
	import { getUserSettings } from '$lib/apis/users';
	import { getTaskIdsByChatId } from '$lib/apis';

	import { ChatSession } from './Chat/session.svelte';
	import { ChatEvents, type EventCallRequest } from './Chat/events';
	import { ChatSend } from './Chat/send';

	import MessageInput from './MessageInput.svelte';
	import Messages from '$lib/components/chat/Messages.svelte';
	import Navbar from './Navbar.svelte';
	import ChatControls from './ChatControls.svelte';
	import EventConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import NotFound from '$lib/components/common/NotFound.svelte';
	import Placeholder from './Placeholder.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	let { chatIdProp = '' }: { chatIdProp?: string } = $props();

	let loading = $state(false);
	// 存在しない・削除済み・他人のチャット。URL を保ったまま 404 表示(2026-07-19 仕様)
	let notFound = $state(false);

	const eventTarget = new EventTarget();
	let controlPane = $state<any>();
	let controlPaneComponent = $state<any>();

	let lastScrollTop = 0;
	let messagesContainerElement = $state<HTMLDivElement>();

	const t = (key: string, options?: Record<string, unknown>) => $i18n.t(key, options);

	// 会話の状態($state)と永続化。受信側(SSE イベント適用)は ChatEvents、
	// 送信側(completion 送信・樹操作)は ChatSend — 分割の全容は Chat/session.svelte.ts
	const session = new ChatSession(t);

	const scrollToBottom = async () => {
		await tick();
		if (messagesContainerElement) {
			messagesContainerElement.scrollTop = messagesContainerElement.scrollHeight;
		}
	};

	// 追従中はコンテンツの高さ変化そのものに張り付く(use: アクション)。
	// デルタ受信時の scrollToBottom だけだと、その後に非同期で高さが伸びる要素
	// (tool 結果の画像・チャート、コードハイライト等)の分が取り残される。
	const pinToBottomOnResize = (node: HTMLElement) => {
		const observer = new ResizeObserver(() => {
			if (session.autoScroll && messagesContainerElement) {
				messagesContainerElement.scrollTop = messagesContainerElement.scrollHeight;
			}
		});
		observer.observe(node);
		return {
			destroy: () => observer.disconnect()
		};
	};

	// __event_call__(confirmation / input)ダイアログ。表示要求は ChatEvents から届く
	let showEventConfirmation = $state(false);
	let eventConfirmationTitle = $state('');
	let eventConfirmationMessage = $state('');
	let eventConfirmationInput = $state(false);
	let eventConfirmationInputPlaceholder = $state('');
	let eventConfirmationInputValue = $state('');
	let eventCallback: ((result: unknown) => Promise<void> | void) | null = null;

	const onEventCall = (
		request: EventCallRequest,
		callback: (result: unknown) => Promise<void> | void
	) => {
		eventCallback = callback;

		eventConfirmationInput = request.input;
		showEventConfirmation = true;

		eventConfirmationTitle = request.title;
		eventConfirmationMessage = request.message;
		if (request.input) {
			eventConfirmationInputPlaceholder = request.placeholder ?? '';
			eventConfirmationInputValue = request.value ?? '';
		}
	};

	const events = new ChatEvents({ session, eventTarget, scrollToBottom, onEventCall });
	const send = new ChatSend({ session, events, t, eventTarget, scrollToBottom });

	let chatIdUnsubscriber: Unsubscriber | undefined;

	// wire v2: 再アタッチ済み(fetch 未解決含む)task の二重アタッチ防止
	const attachedTaskIds = new Set<string>();

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
			notFound = false;

			session.prompt = '';
			session.files = [];

			if (chatIdProp && (await loadChat())) {
				await tick();
				loading = false;

				const storedInput = localStorage.getItem(`chat-input-${chatIdProp}`);
				if (storedInput) {
					try {
						const input = JSON.parse(storedInput);

						session.prompt = input.prompt;
						session.files = input.files;
					} catch (e) {}
				}

				window.setTimeout(() => scrollToBottom(), 0);
				const chatInput = document.getElementById('chat-input');
				chatInput?.focus();
			} else {
				notFound = true;
				loading = false;
			}
		})();
	};

	$effect(() => {
		if (session.selectedModels && chatIdProp !== '') {
			session.saveSessionSelectedModels();
		}
	});

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
				session.prompt = event.data.text;
				inputElement.focus();
			}
		}

		if (event.data.type === 'action:submit') {
			console.debug(event.data.text);

			if (session.prompt !== '') {
				await tick();
				send.submitPrompt(session.prompt);
			}
		}

		if (event.data.type === 'input:prompt:submit') {
			console.debug(event.data.text);

			if (event.data.text !== '') {
				await tick();
				send.submitPrompt(event.data.text);
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
			unregisterChatHandler(registeredChatId, events.chatEventHandler);
			registeredChatId = null;
		}
		if (id) {
			registerChatHandler(id, events.chatEventHandler);
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

		const storedInput = localStorage.getItem(`chat-input-${chatIdProp}`);
		if (storedInput) {
			try {
				const input = JSON.parse(storedInput);
				session.prompt = input.prompt;
				session.files = input.files;
			} catch (e) {
				session.prompt = '';
				session.files = [];
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
			unregisterChatHandler(registeredChatId, events.chatEventHandler);
			registeredChatId = null;
		}
	});

	//////////////////////////
	// Web functions
	//////////////////////////

	const initNewChat = async () => {
		const modelsParam = $page.url.searchParams.get('models');
		const modelParam = $page.url.searchParams.get('model');
		if (modelsParam) {
			session.selectedModels = modelsParam.split(',');
		} else if (modelParam) {
			const urlModels = modelParam.split(',');

			if (urlModels.length === 1) {
				const m = $models.find((m) => m.id === urlModels[0]);
				if (!m) {
					const modelSelectorButton = document.getElementById('model-selector-0-button');
					if (modelSelectorButton) {
						modelSelectorButton.click();
						await tick();

						const modelSelectorInput = document.getElementById('model-search-input');
						if (modelSelectorInput instanceof HTMLInputElement) {
							modelSelectorInput.focus();
							modelSelectorInput.value = urlModels[0];
							modelSelectorInput.dispatchEvent(new Event('input'));
						}
					}
				} else {
					session.selectedModels = urlModels;
				}
			} else {
				session.selectedModels = urlModels;
			}
		} else {
			if (sessionStorage.selectedModels) {
				session.selectedModels = JSON.parse(sessionStorage.selectedModels);
				sessionStorage.removeItem('selectedModels');
			} else {
				if ($settings?.models) {
					session.selectedModels = $settings?.models;
				} else if ($config?.default_models) {
					session.selectedModels = $config?.default_models.split(',');
				}
			}
		}

		session.selectedModels = session.selectedModels.filter((modelId) =>
			$models.map((m) => m.id).includes(modelId)
		);
		if (
			session.selectedModels.length === 0 ||
			(session.selectedModels.length === 1 && session.selectedModels[0] === '')
		) {
			if ($models.length > 0) {
				session.selectedModels = [$models[0].id];
			} else {
				session.selectedModels = [''];
			}
		}

		await showControls.set(false);
		await showCallOverlay.set(false);

		if ($page.url.pathname.includes('/c/')) {
			// upstream 由来: ここの `history` はローカルの chat history(window.history ではない)
			// を指しており state は常に undefined だった — 挙動を変えず undefined を明示する
			window.history.replaceState(undefined, '', `/`);
		}

		session.autoScroll = true;

		await chatId.set('');
		await chatTitle.set('');

		session.history = {
			messages: {},
			currentId: null
		};

		session.chatFiles = [];
		session.params = {};

		if ($page.url.searchParams.get('call') === 'true') {
			showCallOverlay.set(true);
			showControls.set(true);
		}

		if ($page.url.searchParams.get('q')) {
			session.prompt = $page.url.searchParams.get('q') ?? '';

			if (session.prompt) {
				await tick();
				send.submitPrompt(session.prompt);
			}
		}

		session.selectedModels = session.selectedModels.map((modelId) =>
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
		// 404(存在しない・削除済み・他人)は握りつぶし、呼び出し側が notFound 表示にする
		session.chat = await getChatById($chatId).catch(() => null);

		if (session.chat) {
			const chatContent = session.chat.chat;

			if (chatContent) {
				session.selectedModels = chatContent.models;
				session.history = chatContent.history;

				chatTitle.set(chatContent.title);

				const userSettings = await getUserSettings();

				if (userSettings) {
					await settings.set(userSettings.ui);
				} else {
					await settings.set(JSON.parse(localStorage.getItem('settings') ?? '{}'));
				}

				session.params = chatContent?.params ?? {};
				session.chatFiles = chatContent?.files ?? [];

				session.autoScroll = true;
				await tick();

				const taskRes = await getTaskIdsByChatId($chatId).catch(() => {
					return null;
				});

				if (taskRes) {
					session.taskIds = taskRes.task_ids;

					// wire v2: 走行中タスクへ再アタッチ(SSE)。このモジュールが元 POST の
					// ストリームを消費中(ページ内遷移で戻ってきた等)なら重複購読しない。
					// attachedTaskIds は「アタッチ fetch が未解決の間」の二重発火ガード
					// (getActiveTaskIds は接続確立後にしか現れない)。
					const activeTaskIds = getActiveTaskIds($chatId);
					for (const taskId of session.taskIds ?? []) {
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
				if (session.history.currentId && (taskRes?.task_ids ?? []).length === 0) {
					for (const message of Object.values(session.history.messages)) {
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
			eventCallback?.(inputValue);
		} else {
			eventCallback?.(true);
		}
	}}
	onCancel={() => {
		eventCallback?.(false);
	}}
/>

<div
	class="h-screen max-h-[100dvh] transition-width duration-200 ease-in-out {$showSidebar
		? '  md:max-w-[calc(100%-var(--sidebar-width))]'
		: ' '} w-full max-w-full flex flex-col"
	id="chat-container"
>
	{#if notFound}
		<NotFound message={t('Chat not found')} />
	{:else if !loading}
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
							models: session.selectedModels,
							system: undefined,
							params: session.params,
							history: session.history,
							timestamp: Date.now()
						}
					}}
					history={session.history}
					bind:selectedModels={session.selectedModels}
					shareEnabled={!!session.history.currentId}
					{initNewChat}
				/>

				<div class="flex flex-col flex-auto z-10 w-full @container">
					{#if $settings?.landingPageMode === 'chat' || createMessagesList(session.history, session.history.currentId).length > 0}
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
								if (!el) {
									return;
								}
								if (el.scrollHeight - el.scrollTop <= el.clientHeight + 50) {
									session.autoScroll = true;
								} else if (el.scrollTop < lastScrollTop) {
									session.autoScroll = false;
								}
								lastScrollTop = el.scrollTop;
							}}
						>
							<div class=" h-full w-full flex flex-col" use:pinToBottomOnResize>
								<Messages
									chatId={$chatId}
									bind:history={session.history}
									bind:autoScroll={session.autoScroll}
									bind:prompt={session.prompt}
									selectedModels={session.selectedModels}
									atSelectedModel={session.atSelectedModel}
									sendPrompt={send.sendPrompt}
									showMessage={send.showMessage}
									continueResponse={send.continueResponse}
									regenerateResponse={send.regenerateResponse}
									chatActionHandler={events.chatActionHandler}
									addMessages={send.addMessages}
									bottomPadding={session.files.length > 0}
								/>
							</div>
						</div>

						<div class=" pb-[1rem]">
							<MessageInput
								history={session.history}
								taskIds={session.taskIds}
								selectedModels={session.selectedModels}
								bind:files={session.files}
								bind:prompt={session.prompt}
								bind:autoScroll={session.autoScroll}
								bind:atSelectedModel={session.atSelectedModel}
								transparentBackground={!!$settings?.backgroundImageUrl}
								stopResponse={send.stopResponse}
								createMessagePair={send.createMessagePair}
								onChange={(input) => {
									if (input.prompt) {
										localStorage.setItem(`chat-input-${$chatId}`, JSON.stringify(input));
									} else {
										localStorage.removeItem(`chat-input-${$chatId}`);
									}
								}}
								onSubmit={async (userPrompt) => {
									if (userPrompt || session.files.length > 0) {
										await tick();
										send.submitPrompt(userPrompt);
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
								history={session.history}
								selectedModels={session.selectedModels}
								bind:files={session.files}
								bind:prompt={session.prompt}
								bind:autoScroll={session.autoScroll}
								bind:atSelectedModel={session.atSelectedModel}
								transparentBackground={!!$settings?.backgroundImageUrl}
								stopResponse={send.stopResponse}
								createMessagePair={send.createMessagePair}
								onSubmit={async (userPrompt) => {
									if (userPrompt || session.files.length > 0) {
										await tick();
										send.submitPrompt(userPrompt);
									}
								}}
							/>
						</div>
					{/if}
				</div>
			</Pane>

			<ChatControls
				bind:this={controlPaneComponent}
				bind:history={session.history}
				bind:chatFiles={session.chatFiles}
				bind:files={session.files}
				bind:pane={controlPane}
				chatId={$chatId}
				modelId={session.selectedModelIds?.at(0) ?? null}
				submitPrompt={send.submitPrompt}
				stopResponse={send.stopResponse}
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
