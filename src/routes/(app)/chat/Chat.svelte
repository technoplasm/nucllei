<script lang="ts">
	import { PaneGroup, Pane } from 'paneforge';

	import { getContext, onDestroy, onMount, tick, untrack } from 'svelte';
	const i18n: Writable<i18nType> = getContext('i18n');

	import { toast } from 'svelte-sonner';

	import { page } from '$app/stores';
	import { goto } from '$app/navigation';

	import { type Unsubscriber, type Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import {
		chatId,
		chats,
		config,
		currentChatPage,
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
		getActiveTaskIds,
		selectAttachableTaskIds
	} from '$lib/utils/completion-streams';

	import { cloneChatById, getChatById, getChatList } from '$lib/apis/chats';
	import { getUserSettings } from '$lib/apis/users';
	import { getTaskIdsByChatId } from '$lib/apis';

	import { resetChatUsage, setChatUsageBaseline } from '$lib/stores/live-usage.svelte';
	import { clearChatProgress } from '$lib/stores/tool-progress.svelte';
	import { clearChatChildren, refreshChatChildren } from '$lib/stores/chat-children.svelte';
	import {
		clearExecutions,
		executionsState,
		EXECUTION_POLL_MS,
		isExecutionsRefreshBackedOff,
		refreshExecutions,
		shouldPollExecutions
	} from '$lib/stores/executions.svelte';

	import { ChatSession } from './Chat/session.svelte';
	import { ChatEvents, type EventCallRequest } from './Chat/events';
	import { ChatSend } from './Chat/send';

	import MessageInput from './MessageInput.svelte';
	import Messages from '$lib/components/chat/Messages.svelte';
	import Navbar from './Navbar.svelte';
	import ChatControls from './ChatControls.svelte';
	import EventConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import NotFound from '$lib/components/common/NotFound.svelte';
	import { overlayScrollbar } from '$lib/actions/overlay-scrollbars';
	import Placeholder from './Placeholder.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	let { chatIdProp = '' }: { chatIdProp?: string } = $props();

	let loading = $state(false);
	// 存在しない・削除済み・他人のチャット。URL を保ったまま 404 表示(2026-07-19 仕様)
	let notFound = $state(false);

	// llens(#107 P3): /c/ の観測モード。kind="agent" の chat(subagent session)は
	// readOnly で表示する — MessageInput 非表示・send/regenerate/edit 系導線なし。
	// live 追従(task 再アタッチ・envelope 適用)はそのまま効く。
	let isAgentSession = $state(false);
	let parentChatId = $state<string | null>(null);

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

	const events = new ChatEvents({
		session,
		eventTarget,
		scrollToBottom,
		onEventCall,
		t,
		// 観測モードでは done の completed / 保存経路を駆動しない(events.ts 参照)
		isObserver: () => isAgentSession,
		// llens(#110): BE のサーバ駆動配達(通知ターンの直接 append)は FE の知らない
		// message_id の封筒を生む — exec 行のある chat は未知 id で history を読み直す
		hasExecutionRows: () =>
			executionsState.chatId === $chatId && executionsState.entries.length > 0,
		// llens(#110): turn 終了(completed / 保存済み)で exec 行を読み直す —
		// turn 中に完了した execution を 5 秒 tick を待たず拾う
		onTurnEnd: () => {
			const targetChatId = $chatId;
			if (targetChatId && !loading && !notFound && !isAgentSession) {
				void refreshExecutions(targetChatId);
			}
		}
	});
	const send = new ChatSend({ session, events, t, eventTarget, scrollToBottom });

	// llens(#110): exec 行の 5 秒ポーリング(SSE push はしない仕様 — 既存慣行)。
	// 自動再開はサーバ駆動(契約 T3)— FE は状態表示と BE 発ターンの発見だけを持つ。
	// 述語 P(shouldPollExecutions — executions store)は cadence 制御のみ:
	// seed は loadChat が P を通さず必ず打つ(自己接地)。
	// 各 tick で getTaskIdsByChatId も読み、BE 発の続きターン(配達後の自動再開)へ
	// 未アタッチなら attachToTask する — ストリームが FE に表示される。
	const pollExecutions = async () => {
		// バックグラウンドタブでは休止(復帰後の次 tick で追いつく)
		if (document.visibilityState === 'hidden') {
			return;
		}
		const targetChatId = $chatId;
		if (!targetChatId || loading || notFound || isAgentSession) {
			return;
		}
		// B-2: 連続失敗中は tick を間引く(backend 断で 5 秒固定連打しない)
		if (isExecutionsRefreshBackedOff(Date.now())) {
			return;
		}
		if (
			!shouldPollExecutions(targetChatId, (session.taskIds ?? []).length > 0, Date.now() / 1000)
		) {
			return;
		}
		// B-6: tick は有界の active 応答(store 側でマージ)— 全量は seed が担う
		const { deliveredAdvanced } = await refreshExecutions(targetChatId, { activeOnly: true });
		if (deliveredAdvanced && $chatId === targetChatId) {
			// 非表示タブ休止中(または poll 間隙)に配達が完走した — 続きターンの封筒は
			// もう来ないので、history を読み直して notice + 続きターンへ追いつく
			events.refetchChat(targetChatId);
		}
		await attachNewChatTasks(targetChatId);
	};

	// llens(#110): BE 発ターン(サーバ駆動の続きターン)の発見とアタッチ。
	// loadChat の再アタッチと同じ二段ガード(activeTaskIds / attachedTaskIds)に加え、
	// 送信直後の自ターン(task イベント未着で taskId 未確定 = getActiveTaskIds に
	// 現れない)が居る tick は全件スキップ — 判定は selectAttachableTaskIds に集約
	// (fetch の await 中に送信が始まる窓があるため、判定は fetch 解決後に行う)。
	// 履歴の追いつき自体は未知 message_id 封筒の refetch ゲート(events.ts)が担う
	const attachNewChatTasks = async (targetChatId: string) => {
		const taskRes = await getTaskIdsByChatId(targetChatId).catch(() => null);
		if (!taskRes || $chatId !== targetChatId) {
			return;
		}
		const newTaskIds = selectAttachableTaskIds(
			targetChatId,
			taskRes.task_ids ?? [],
			attachedTaskIds
		);
		if (newTaskIds.length === 0) {
			return;
		}
		session.taskIds = taskRes.task_ids;
		for (const taskId of newTaskIds) {
			attachedTaskIds.add(taskId);
			attachToTask(targetChatId, taskId).catch((error) => {
				console.error('chat task attach failed', taskId, error);
			});
		}
	};

	$effect(() => {
		if (loading || notFound || isAgentSession) {
			return;
		}
		// chat 切替・unmount は effect teardown が確実に止める(agent session の
		// 観測ポーリングと同じ作法)。tick 側が $chatId / 状態を都度読むので
		// interval は張りっぱなしでよい(seed は loadChat が P を通さず必ず打つ)
		untrack(() => void pollExecutions());
		const interval = window.setInterval(() => void pollExecutions(), EXECUTION_POLL_MS);
		return () => {
			window.clearInterval(interval);
		};
	});

	// fork = clone(BE が kind=user へ落とす — 「介入したいときは fork」)。
	// ChatItem / SubagentSessionBlock の clone 導線と同じ後処理(サイドバー一覧の追従)
	const forkChatHandler = async () => {
		const res = await cloneChatById(
			$chatId,
			t('Clone of {{TITLE}}', { TITLE: $chatTitle || $chatId })
		).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			goto(`/c/${res.id}`);
			currentChatPage.set(1);
			await chats.set(await getChatList($currentChatPage));
		}
	};

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

		// 新規チャット画面(chatIdProp なし)へ前のチャットの $chatId を持ち越さない。
		// /c/<id> からロゴ(href="/")やブラウザの「戻る」で / に来ると、この Chat は
		// 新しくマウントされるのに $chatId は前のチャットのまま — 下の購読が張られず
		// initNewChat も走らないので、開始画面なのに前チャットのコンテキスト量ゲージ
		// (UsageChip)が出ていた。'' にして下の購読の初期発火で initNewChat へ入る。
		if (!chatIdProp && $chatId) {
			await chatId.set('');
		}

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

		// 購読直後の初期発火では pane 未登録のまま collapse() が走り、paneforge の
		// "Assertion failed" が console を汚す(assert は console.error なので try/catch
		// では消えない)。Switch.svelte の lastState ガードパターンに倣い、値が実際に
		// 変化したときだけ pane を操作する。初期値は常に false(stores の writable(false)・
		// 永続化なし。ChatControls の onDestroy も false へ戻す)なので、初期発火を
		// スキップしても「閉じたまま」の既定挙動は変わらない。
		let lastShowControls: boolean | null = null;
		showControlsUnsubscriber = showControls.subscribe(async (value) => {
			const changed = lastShowControls !== null && lastShowControls !== value;
			lastShowControls = value;

			if (changed && controlPane && !$mobile) {
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

		// llens(#107 P3): 新規チャットは通常モード。サイドバー tree も畳む
		isAgentSession = false;
		parentChatId = null;
		clearChatChildren();
		// llens(#110): 前 chat の exec 行キャッシュ(バックグラウンドチップ)を持ち越さない
		clearExecutions();

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
		} else if ($page.url.searchParams.get('draft')) {
			// llens(#121): ?draft= は入力欄にプリフィルするだけで送信しない(?q= は自動送信)。
			// サイドバーの「チャットで調べる」が使う — 利用者が文面を直してから送る前提
			session.prompt = $page.url.searchParams.get('draft') ?? '';
			await tick();
			document.getElementById('chat-input')?.focus();
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

			// llens(#107 P3): agent session(subagent の子 chat)は観測モードで開く。
			// サイドバー tree は「表示中の user chat の children」— agent session を
			// 観測中はその親の children を保持し、tree は親項目の下に出続ける
			isAgentSession = session.chat.kind === 'agent';
			parentChatId = session.chat.parent_chat_id ?? null;
			refreshChatChildren(isAgentSession ? parentChatId : $chatId);

			if (chatContent) {
				session.selectedModels = chatContent.models;
				session.history = chatContent.history;

				// llens(#102): 揮発 usage はチャット切替でリセットし、確定値を floating
				// ゲージの baseline に復元する。leaf message の usage(マージ済み — emit と
				// 同じ形)を優先し、無ければ meta.usage(会話累積)。生成中チャットへの
				// 再アタッチ分はこのあとの attachToTask で backend が最新 chat:usage を
				// 再送してくる。
				resetChatUsage($chatId);
				setChatUsageBaseline(
					$chatId,
					chatContent.history?.messages?.[chatContent.history?.currentId]?.usage ??
						session.chat.meta?.usage ??
						null
				);

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

				// llens(#107 W-A): 生きた task が無ければ、このチャットの tool 進捗は確定済み
				// の残骸 — チャット単位で掃く(done を通らず clearMessageProgress されなかった
				// 分のリーク対策)。task が生きている間は消さない: 非表示中に fallback handler
				// (+layout.svelte)が記録した進捗が唯一の復元経路。
				if ((taskRes?.task_ids ?? []).length === 0) {
					clearChatProgress($chatId);
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

				// llens(#110): exec 行の seed(loadChat 経路)。poll の述語 P を通さず
				// 必ず打つ — リロード後の未配達 terminal・running の検知はここが起点
				// (fire-and-forget: チップ・poll 述語は store が追従)。
				// 観測モード(agent session)は対象外 — 子 chat に exec は無い(設計 D9)
				clearExecutions();
				if (!isAgentSession) {
					void refreshExecutions($chatId);
				}

				await tick();

				return true;
			} else {
				return null;
			}
		}

		// 取得失敗(notFound 行き)。前のチャットの tree もバックグラウンドチップも残さない
		isAgentSession = false;
		parentChatId = null;
		clearChatChildren();
		clearExecutions();
	};

	// llens(#107 P3): /c/ 観測モードの追いつきポーリング。loadChat はロード時点の走行
	// task にしかアタッチしないため、観測開始後に親が同一 session を継続 turn で再駆動
	// しても FE へ push は来ず、view が黙って stale 化する。isAgentSession の間だけ
	// 軽量ポーリングで新 task を発見し、アタッチ + history の追いつき再取得を行う。
	const AGENT_SESSION_POLL_MS = 5000;
	const pollAgentSessionTasks = async () => {
		// バックグラウンドタブでは何もしない(復帰後の次 tick で追いつく)
		if (document.visibilityState === 'hidden') {
			return;
		}
		const observedChatId = $chatId;
		if (!observedChatId || !isAgentSession) {
			return;
		}
		const taskRes = await getTaskIdsByChatId(observedChatId).catch(() => null);
		if (!taskRes || $chatId !== observedChatId) {
			return;
		}
		// loadChat の再アタッチと同じ二段ガード: このモジュールが消費中のストリームと、
		// アタッチ fetch 未解決の task には重複アタッチしない
		const activeTaskIds = getActiveTaskIds(observedChatId);
		const newTaskIds = (taskRes.task_ids ?? []).filter(
			(taskId: string) => !activeTaskIds.includes(taskId) && !attachedTaskIds.has(taskId)
		);
		if (newTaskIds.length === 0) {
			return;
		}
		session.taskIds = taskRes.task_ids;
		for (const taskId of newTaskIds) {
			attachedTaskIds.add(taskId);
			attachToTask(observedChatId, taskId).catch((error) => {
				console.error('agent session task attach failed', taskId, error);
			});
		}
		// 新 task の発見 = 継続 turn が始まっている。SSE スナップショットは走行中 turn の
		// message しか運ばないため、fetch 済み history を読み直して追いつかせる
		// (events.ts の観測モード refetch と同じ理由。以降の封筒適用で収束する)
		const res = await getChatById(observedChatId).catch(() => null);
		if (res?.chat?.history && $chatId === observedChatId) {
			session.history = res.chat.history;
		}
	};

	$effect(() => {
		if (!isAgentSession) {
			return;
		}
		// chat 切替(isAgentSession の変化)・unmount で effect teardown が確実に止める。
		// 観測対象の切替は poll 側が $chatId を都度読むので interval は張りっぱなしでよい
		const interval = window.setInterval(() => void pollAgentSessionTasks(), AGENT_SESSION_POLL_MS);
		return () => {
			window.clearInterval(interval);
		};
	});

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
					chatFiles={session.chatFiles}
					bind:selectedModels={session.selectedModels}
					shareEnabled={!!session.history.currentId}
					{initNewChat}
				/>

				{#if isAgentSession}
					<!-- llens(#107 P3): 観測モードのバナー — read-only 表示 + 親 chat リンク + fork。
					 Navbar は sticky z-30 + -mb-8 で後続の上 32px に被さるため、mt-8 で実ボックス直下へ
					 押し下げ(クリック横取り回避)、z-40 で nav 下端のフェードグラデより上に描く -->
					<div class="flex justify-center z-40 mt-8 px-4 pt-1.5">
						<div
							class="flex items-center gap-x-3 gap-y-1 flex-wrap justify-center rounded-lg border border-line px-3 py-1.5 text-sm text-content-muted min-w-0"
						>
							<div class="flex items-center gap-1.5 min-w-0">
								<span class="i-lucide-bot size-4 block shrink-0"></span>
								<span class="font-medium text-content truncate">{t('Agent session')}</span>
								<span class="text-xs shrink-0">({t('Read-only')})</span>
							</div>
							{#if parentChatId}
								<a
									class="flex items-center gap-1 shrink-0 hover:text-content transition"
									href="/c/{parentChatId}"
								>
									<span class="i-lucide-corner-left-up size-3.5 block"></span>
									{t('Parent chat')}
								</a>
							{/if}
							<button
								class="flex items-center gap-1 shrink-0 hover:text-content transition"
								onclick={forkChatHandler}
							>
								<span class="i-lucide-git-branch size-3.5 block"></span>
								{t('Fork')}
							</button>
						</div>
					</div>
				{/if}

				<div class="flex flex-col flex-auto z-10 w-full @container">
					{#if isAgentSession || $settings?.landingPageMode === 'chat' || createMessagesList(session.history, session.history.currentId).length > 0}
						<!-- OverlayScrollbars はラッパをホストに、既存のスクロール要素を viewport として張る
						 (childViewport)— bind/onscroll/id の scroll ロジックは無改修で生きる -->
						<div
							class="flex flex-col w-full flex-auto h-0 max-w-full z-10"
							use:overlayScrollbar={{ childViewport: true }}
						>
							<div
								class=" pb-2.5 flex flex-col justify-between w-full h-full max-w-full overflow-auto"
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
										readOnly={isAgentSession}
										bind:history={session.history}
										bind:autoScroll={session.autoScroll}
										bind:prompt={session.prompt}
										selectedModels={session.selectedModels}
										atSelectedModel={session.atSelectedModel}
										sendPrompt={send.sendPrompt}
										continueResponse={send.continueResponse}
										regenerateResponse={send.regenerateResponse}
										chatActionHandler={events.chatActionHandler}
										addMessages={send.addMessages}
										bottomPadding={session.files.length > 0}
									/>
								</div>
							</div>
						</div>

						{#if !isAgentSession}
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
						{/if}
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
