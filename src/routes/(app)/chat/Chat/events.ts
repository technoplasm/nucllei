/**
 * チャットの受信側(#70 C トラック: Chat.svelte 分割)。
 *
 * wire v2 の SSE イベント封筒(completion-streams)と、completed / actions 応答に
 * 同梱されるバッファ済みイベントを message へ適用する。message の状態だけを変える
 * イベントは純関数層(chat-events.ts applyMessageMutation)へ委譲し、ここは
 * 副作用(toast・TTS イベント・自動スクロール・__event_call__ ダイアログ・
 * done 時の completed 経路)を駆動する。状態は ChatSession を介して共有する。
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
import type { CompletionEnvelope, StreamContext } from '$lib/utils/completion-streams';
import { getChatList, updateChatById } from '$lib/apis/chats';
import { chatCompleted, chatAction } from '$lib/apis';

import { type ChatSession } from './session.svelte';

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

	constructor({
		session,
		eventTarget,
		scrollToBottom,
		onEventCall
	}: {
		session: ChatSession;
		eventTarget: EventTarget;
		scrollToBottom: () => Promise<void> | void;
		onEventCall: (
			request: EventCallRequest,
			callback: (result: unknown) => Promise<void> | void
		) => void;
	}) {
		this.#session = session;
		this.#eventTarget = eventTarget;
		this.#scrollToBottom = scrollToBottom;
		this.#onEventCall = onEventCall;
	}

	// wire v2: completion-streams(SSE)から届くイベント封筒を message へ適用する。
	// 表示中チャットのハンドラとして registerChatHandler で登録される
	// (登録・解除が同一参照で行えるようアロー・フィールドで束縛している)。
	chatEventHandler = async (envelope: CompletionEnvelope, context: StreamContext) => {
		const messageId = envelope.message_id;
		if (envelope.chat_id !== get(chatId) || !messageId) {
			return;
		}

		await tick();
		const message = this.#session.history.messages[messageId];
		if (!message) {
			return;
		}

		const type = envelope?.data?.type ?? null;
		const data = envelope?.data?.data ?? null;
		// __event_call__(confirmation/input)への応答は call_id 付き封筒に対する
		// POST /api/tasks/{task_id}/calls/{call_id}(context.reply)で返す
		const callId = envelope.call_id;
		const reply = callId ? (result: unknown) => context.reply(callId, result) : null;

		await this.#applyMessageEvent(type, data, message, envelope.chat_id, reply);
		this.#session.history.messages[messageId] = message;
	};

	// completed / actions の応答 JSON に同梱される events(outlet filter / action の
	// emit — 封筒なしの {type, data} 形)も SSE と同じ適用関数を通す。
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

	#applyMessageEvent = async (
		type: string | null,
		data: any,
		message: ChatMessage,
		targetChatId: string,
		reply: ((result: unknown) => Promise<void>) | null
	) => {
		// message の状態だけを変えるイベント(status / task-cancelled / delta / files /
		// source 等)は純関数層(chat-events.ts)で適用する
		if (applyMessageMutation(type, data, message)) {
			return;
		}

		if (type === 'chat:completion') {
			await this.#chatCompletionEventHandler(data, message, targetChatId);
		} else if (type === 'chat:title') {
			chatTitle.set(data);
			currentChatPage.set(1);
			await chats.set(await getChatList(get(currentChatPage)));
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
	#chatCompletionEventHandler = async (
		data: CompletionData,
		message: ChatMessage,
		targetChatId: string
	) => {
		const { contentChanged, done, error } = applyCompletionData(message, data);

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
			await this.chatCompletedHandler(
				targetChatId,
				message.model,
				message.id,
				createMessagesList(this.#session.history, message.id)
			);
		}

		if (this.#session.autoScroll) {
			this.#scrollToBottom();
		}
	};

	chatCompletedHandler = async (
		targetChatId: string,
		modelId: string | undefined,
		responseMessageId: string,
		messages: ChatMessage[]
	) => {
		const session = this.#session;

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
				if (message?.id) {
					// Add null check for message and message.id
					session.history.messages[message.id] = {
						...session.history.messages[message.id],
						...(session.history.messages[message.id].content !== message.content
							? { originalContent: session.history.messages[message.id].content }
							: {}),
						...message
					};
				}
			}
		}

		// wire v2: outlet filter が emit したイベント(status 等)は応答 JSON の
		// events に同梱される — SSE と同じ適用関数で処理する
		if (res !== null) {
			await this.applyBufferedEvents(res.events, responseMessageId, targetChatId);
		}

		await tick();

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

		// wire v2: action / outlet filter の emit は応答 JSON の events に同梱される
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
		}
	};
}
