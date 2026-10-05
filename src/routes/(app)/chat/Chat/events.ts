/**
 * チャットの受信側(#70 C トラック: Chat.svelte 分割)。
 *
 * wire v2 の SSE イベント封筒(completion-streams)と、actions 応答に
 * 同梱されるバッファ済みイベントを message へ適用する。message の状態だけを変える
 * イベントは純関数層(chat-events.ts applyMessageMutation)へ委譲し、ここは
 * 副作用(toast・TTS イベント・自動スクロール・__event_call__ ダイアログ・
 * done 時の保存経路)を駆動する。状態は ChatSession を介して共有する。
 */
import { tick } from 'svelte';
import { get } from 'svelte/store';
import { toast } from 'svelte-sonner';

import {
	chatId,
	chats,
	chatTitle,
	config,
	currentChatPage,
	models,
	settings,
	showCallOverlay
} from '$lib/stores';
import { getMessageContentParts, createMessagesList } from '$lib/utils';
import {
	applyCompletionData,
	applyMessageMutation,
	type CompletionData
} from '$lib/utils/chat-events';
import type { ChatMessage } from '$lib/types/chat';
import { clearMessageProgress, setToolProgress } from '$lib/stores/tool-progress.svelte';
import {
	clearLiveUsage,
	markLiveUsagePending,
	setChatUsageBaseline,
	setLiveUsage
} from '$lib/stores/live-usage.svelte';
import { refreshChatChildren, refreshCurrentChatChildren } from '$lib/stores/chat-children.svelte';
import { applyExecutionDone, refreshExecutions } from '$lib/stores/executions.svelte';
import type { CompletionEnvelope, StreamContext } from '$lib/utils/completion-streams';
import { getChatById, getChatList, updateChatById } from '$lib/apis/chats';
import { chatAction } from '$lib/apis';

import { type ChatSession, type Translate } from './session.svelte';

// __event_call__(confirmation / input)のダイアログ表示要求。表示自体は
// Chat.svelte の EventConfirmDialog が担う
export type EventCallRequest = {
	input: boolean;
	title: string;
	message: string;
	placeholder?: string;
	value?: string;
};

export class ChatEvents {
	#session: ChatSession;
	#eventTarget: EventTarget;
	#scrollToBottom: () => Promise<void> | void;
	#onEventCall: (
		request: EventCallRequest,
		callback: (result: unknown) => Promise<void> | void
	) => void;
	// llens(#107 P3): /c/ の agent session 観測モード。true の間は done の
	// 完了時の保存経路(updateChatById)を駆動しない —
	// 子ターンの完了処理・永続化は BE の内部 runner(subagent)が持ち、
	// agent session への updateChatById は BE が 400 で拒否する。
	#isObserver: () => boolean;
	// llens(#110): turn 終了時の再評価フック。Chat.svelte が exec 行を読み直す契機
	// (turn 中に完了した execution を 5 秒 tick を待たず拾う)。
	#onTurnEnd: () => void;
	// llens(#110): 表示中 chat に exec 行があるか。BE のサーバ駆動配達(通知ターン)は
	// 履歴へ直接 append されるため、未知 message_id の封筒 refetch ゲートを
	// 観測モードに加えて exec のある chat にも開放する。
	#hasExecutionRows: () => boolean;

	#t: Translate;

	constructor({
		session,
		eventTarget,
		scrollToBottom,
		onEventCall,
		isObserver,
		onTurnEnd,
		hasExecutionRows,
		t
	}: {
		session: ChatSession;
		eventTarget: EventTarget;
		scrollToBottom: () => Promise<void> | void;
		onEventCall: (
			request: EventCallRequest,
			callback: (result: unknown) => Promise<void> | void
		) => void;
		isObserver?: () => boolean;
		onTurnEnd?: () => void;
		hasExecutionRows?: () => boolean;
		t?: Translate;
	}) {
		this.#session = session;
		this.#eventTarget = eventTarget;
		this.#scrollToBottom = scrollToBottom;
		this.#onEventCall = onEventCall;
		this.#isObserver = isObserver ?? (() => false);
		this.#onTurnEnd = onTurnEnd ?? (() => {});
		this.#hasExecutionRows = hasExecutionRows ?? (() => false);
		this.#t = t ?? ((key: string) => key);
	}

	// wire v2: completion-streams(SSE)から届くイベント封筒を message へ適用する。
	// 表示中チャットのハンドラとして registerChatHandler で登録される
	// (登録・解除が同一参照で行えるようアロー・フィールドで束縛している)。
	//
	// 高頻度イベント(delta / chat:usage 等)は**同期で適用して戻り値なし**
	// (await を挟むと 1 イベントごとに microtask 境界 = Svelte の flush が入り、
	// BE が速いときの適用がレンダリング律速で滞留する — 生成完了後も「準備中」の
	// まま固まって見える実害)。副作用が非同期のイベント(done の completed 経路・
	// chat:title 等)だけ Promise を返し、consumeCompletionStream が順序を保って待つ。
	chatEventHandler = (
		envelope: CompletionEnvelope,
		context: StreamContext
	): void | Promise<void> => {
		const messageId = envelope.message_id;
		if (envelope.chat_id !== get(chatId) || !messageId) {
			return;
		}

		const message = this.#session.history.messages[messageId];
		if (!message) {
			// 観測モード: BE の内部 runner が子 history へ直接メッセージを積むため、
			// fetch 済み history に無い message_id の封筒は正規に届く。無言 drop すると
			// アタッチ済みでも view が凍る — history を読み直して追いつく
			// (SubagentSessionBlock.refetchForUnknownMessage と同じパターン。読み直し中に
			// 落ちたイベントは後続の間引きスナップショット到着で収束する)。
			// llens(#110): exec 行のある chat も同様 — BE のサーバ駆動配達(通知ターン)は
			// 履歴へ直接 append され、その続きターンの封筒は FE の知らない id で届く。
			// それ以外の通常チャットでは従来どおり無視(自分の送信経路で必ず既知の id が
			// 積まれている)。in-flight 間引き(#observerRefetching)が throttle を兼ねる。
			if (this.#isObserver() || this.#hasExecutionRows()) {
				this.#refetchObservedChat(get(chatId));
			}
			return;
		}

		const type = envelope?.data?.type ?? null;
		const data = envelope?.data?.data ?? null;
		// __event_call__(confirmation/input)への応答は call_id 付き封筒に対する
		// POST /api/tasks/{task_id}/calls/{call_id}(context.reply)で返す
		const callId = envelope.call_id;
		const reply = callId ? (result: unknown) => context.reply(callId, result) : null;

		const result = this.#applyMessageEvent(type, data, message, envelope.chat_id, reply);
		this.#session.history.messages[messageId] = message;
		return result;
	};

	// llens(#110): poll(Chat.svelte)が delivered の増分を観測したときの追いつき口。
	// 非表示タブ休止中に配達(通知ターン)が完走すると、復帰後は attach 対象の task が
	// もう無く封筒 refetch が発火しない — 同じ読み直し(in-flight 間引き込み)を貸す
	refetchChat = (targetChatId: string) => {
		this.#refetchObservedChat(targetChatId);
	};

	// 観測モードで未知 message_id が届いたときの history 読み直し。走行中(in-flight)は
	// 1 本に間引く多重発火ガード付き — 高頻度封筒ごとに GET を積まない
	#observerRefetching = false;
	#refetchObservedChat = (targetChatId: string) => {
		if (this.#observerRefetching) {
			return;
		}
		this.#observerRefetching = true;
		getChatById(targetChatId)
			.then((res) => {
				// 読み直し中にチャットを離れていたら捨てる(別チャットの history を上書きしない)
				if (res?.chat?.history && get(chatId) === targetChatId) {
					this.#session.history = res.chat.history;
				}
			})
			.catch(() => null)
			.finally(() => {
				this.#observerRefetching = false;
			});
	};

	// actions の応答 JSON に同梱される events(action の emit — 封筒なしの
	// {type, data} 形)も SSE と同じ適用関数を通す。
	applyBufferedEvents = async (
		events: { type?: string; data?: any }[] | undefined,
		messageId: string,
		targetChatId: string
	) => {
		const message = this.#session.history.messages[messageId];
		if (!message || !((events?.length ?? 0) > 0)) {
			return;
		}

		for (const event of events ?? []) {
			// バッファ応答経路に __event_call__ は無い(reply 不可)
			await this.#applyMessageEvent(
				event?.type ?? null,
				event?.data ?? null,
				message,
				targetChatId,
				null
			);
		}
		this.#session.history.messages[messageId] = message;
	};

	// 同期で完結するイベントは戻り値なし、非同期の副作用を持つイベントだけ
	// Promise を返す(chatEventHandler のコメント参照)。
	#applyMessageEvent = (
		type: string | null,
		data: any,
		message: ChatMessage,
		targetChatId: string,
		reply: ((result: unknown) => Promise<void>) | null
	): void | Promise<void> => {
		// message の状態だけを変えるイベント(status / task-cancelled / delta / files /
		// source 等)は純関数層(chat-events.ts)で適用する
		if (applyMessageMutation(type, data, message)) {
			return;
		}

		if (type === 'tool:progress') {
			// llens(#100): 実行中 tool の進捗。message には載せない(saveChatHandler が
			// history を丸ごと送るので DB に載り、揮発の規律が破れる)。
			setToolProgress(targetChatId, message.id, data);
		} else if (type === 'execution:done') {
			// llens(#140 UI 追従): background 実行の終端 push。行を即座に終端へ書き換えて
			// カードのスピナーを止め、配達状態(delivered)は直後の読み直しで揃える
			// (5 秒ポーリングは stream の無いときの fallback)
			if (applyExecutionDone(targetChatId, data ?? {})) {
				void refreshExecutions(targetChatId, { activeOnly: true });
			}
		} else if (type === 'chat:usage') {
			// llens(#102): 生成中のトークン使用量(~0.5 秒間隔・揮発)。tool:progress と
			// 同じ規律で message には載せない。
			setLiveUsage(targetChatId, message.id, data);
		} else if (type === 'chat:completion') {
			return this.#chatCompletionEventHandler(data, message, targetChatId);
		} else if (type === 'chat:title') {
			chatTitle.set(data);
			currentChatPage.set(1);
			// llens(#107 P3): サイドバー tree の追従(fire-and-forget — 適用順序に依存しない)。
			// 観測モード(targetChatId = agent session)では保持中の親の children を更新する
			void (this.#isObserver() ? refreshCurrentChatChildren() : refreshChatChildren(targetChatId));
			return getChatList(get(currentChatPage)).then((list) => chats.set(list));
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
			this.#onEventCall(
				{
					input: false,
					title: data.title,
					message: data.message
				},
				reply
			);
		} else if (type === 'input') {
			if (!reply) {
				console.warn('input event without reply channel', data);
				return;
			}
			this.#onEventCall(
				{
					input: true,
					title: data.title,
					message: data.message,
					placeholder: data.placeholder,
					value: data?.value ?? ''
				},
				reply
			);
		} else {
			console.warn('Unknown event type', type, data);
		}
	};

	// wire v2: chat:completion の data(スナップショット / delta / done / error / usage /
	// sources)の message への適用は純関数 applyCompletionData に一元化。ここは
	// 戻りフラグで副作用(TTS・haptic・自動スクロール・done 時の completed 経路)を駆動する。
	// 高頻度の delta / スナップショットは同期で完結し、done のときだけ Promise を返す。
	#chatCompletionEventHandler = (
		data: CompletionData,
		message: ChatMessage,
		targetChatId: string
	): void | Promise<void> => {
		const { contentChanged, done, error } = applyCompletionData(message, data);

		if (!done && message.done !== true) {
			// llens(#102): ターン進行中。最初の chat:usage(プリフィル後)が来るまで、
			// 既知値の無い新規チャットでも floating ゲージを「計測中」で出し続ける
			markLiveUsagePending(targetChatId, message.id);
		}

		if (data.usage) {
			// llens(#102): マージ済み message.usage(ターン累積 completion/duration・
			// context_size 同梱)— floating ゲージの確定値
			setChatUsageBaseline(targetChatId, data.usage);
		}

		if (error) {
			// {error: {content}} の一形 — message.error は適用済み。ここでは通知のみ
			toast.error(typeof message.error === 'object' ? message.error.content : '');
		}

		if (contentChanged) {
			if (navigator.vibrate && (get(settings)?.hapticFeedback ?? false)) {
				navigator.vibrate(5);
			}

			// Emit chat event for TTS
			const messageContentParts = getMessageContentParts(
				message.content,
				get(config)?.audio?.tts?.split_on ?? 'punctuation'
			);
			messageContentParts.pop();

			// dispatch only last sentence and make sure it hasn't been dispatched before
			if (messageContentParts.length > 0 && messageContentParts.at(-1) !== message.lastSentence) {
				message.lastSentence = messageContentParts.at(-1);
				this.#eventTarget.dispatchEvent(
					new CustomEvent('chat', {
						detail: {
							id: message.id,
							content: messageContentParts.at(-1)
						}
					})
				);
			}
		}

		this.#session.history.messages[message.id] = message;

		if (done) {
			return this.#finishCompletion(message, targetChatId);
		}

		if (this.#session.autoScroll) {
			this.#scrollToBottom();
		}
	};

	// done の後処理(TTS 発話・chat:finish・completed 経路)— ターンに 1 回の低頻度パス
	#finishCompletion = async (message: ChatMessage, targetChatId: string) => {
		// llens(#100): 進捗は揮発。確定したメッセージの分は捨てる(残しても
		// 描画は results 到着で止まるが、セッション中ずっと居座らせない)
		clearMessageProgress(message.id);
		// llens(#102): live usage も揮発 — 破棄時にターン平均 tok/s を lastTurnRate へ残す
		clearLiveUsage(targetChatId, message.id);

		if (get(settings).responseAutoPlayback && !get(showCallOverlay)) {
			await tick();
			document.getElementById(`speak-button-${message.id}`)?.click();
		}

		// Emit chat event for TTS
		const lastMessageContentPart =
			getMessageContentParts(
				message.content,
				get(config)?.audio?.tts?.split_on ?? 'punctuation'
			)?.at(-1) ?? '';
		if (lastMessageContentPart) {
			this.#eventTarget.dispatchEvent(
				new CustomEvent('chat', {
					detail: { id: message.id, content: lastMessageContentPart }
				})
			);
		}
		this.#eventTarget.dispatchEvent(
			new CustomEvent('chat:finish', {
				detail: {
					id: message.id,
					content: message.content
				}
			})
		);

		this.#session.history.messages[message.id] = message;

		if (this.#isObserver()) {
			// 観測モード: completed / 保存経路は駆動しない(#isObserver のコメント参照)。
			// task の畳みと、サイドバー tree の走行スピナー更新(親の children 再取得)のみ
			this.#session.taskIds = null;
			void refreshCurrentChatChildren();
		} else {
			await this.chatCompletedHandler(targetChatId);
		}

		if (this.#session.autoScroll) {
			this.#scrollToBottom();
		}

		// llens(#110): turn の後処理(completed / 保存・taskIds の畳み)まで済んだ地点で
		// 再評価を促す — done フリップ直後に自動送信すると updateChatById が競合する
		this.#onTurnEnd();
	};

	chatCompletedHandler = async (targetChatId: string) => {
		const session = this.#session;

		// Filter の outlet(`/api/chat/completed`)は 2026-10-05 に撤去 — ターン末は
		// 完了時保存とサイドバーの追従だけ(llens の filter は inlet しか使わない)。
		await tick();

		if (get(chatId) == targetChatId) {
			// llens(#110): 完了時保存も saveChatHandler 経由 — BE のサーバ駆動配達
			// (通知ターン append)との競合(400)は接ぎ直しリカバリされる。
			// 保存 + サイドバー一覧の追従は saveChatHandler が持つ
			const saveRes = await session.saveChatHandler(targetChatId, session.history);
			if (!saveRes.ok) {
				toast.error(this.#t('Failed to save the chat.'));
			}

			// llens(#107 P3): このターンで生えた・確定した子 agent session を tree に反映
			await refreshChatChildren(targetChatId);
		}

		session.taskIds = null;
	};

	chatActionHandler = async (
		targetChatId: string,
		actionId: string,
		modelId: string | undefined,
		responseMessageId: string,
		event: unknown = null
	) => {
		const session = this.#session;
		const messages: ChatMessage[] = createMessagesList(session.history, responseMessageId);

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
			model_item: get(models).find((m) => m.id === modelId),
			chat_id: targetChatId,
			id: responseMessageId
		}).catch((error) => {
			toast.error(`${error}`);
			const lastMessage = messages.at(-1);
			if (lastMessage) {
				lastMessage.error = { content: error };
			}
			return null;
		});

		if (res !== null && res.messages) {
			// Update chat history with the new messages
			for (const message of res.messages) {
				session.history.messages[message.id] = {
					...session.history.messages[message.id],
					...(session.history.messages[message.id].content !== message.content
						? { originalContent: session.history.messages[message.id].content }
						: {}),
					...message
				};
			}
		}

		// wire v2: action の emit は応答 JSON の events に同梱される
		if (res !== null) {
			await this.applyBufferedEvents(res.events, responseMessageId, targetChatId);
		}

		if (get(chatId) == targetChatId) {
			session.chat = await updateChatById(targetChatId, {
				models: session.selectedModels,
				messages: messages,
				history: session.history,
				params: session.params,
				files: session.chatFiles
			});

			currentChatPage.set(1);
			await chats.set(await getChatList(get(currentChatPage)));

			// llens(#107 P3): action 経由でも tree を追従(completed 経路と同じ)
			await refreshChatChildren(targetChatId);
		}
	};
}
