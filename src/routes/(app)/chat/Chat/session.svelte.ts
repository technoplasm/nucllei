/**
 * チャット 1 会話分の状態と永続化(#70 C トラック: Chat.svelte 分割)。
 *
 * $state フィールドの束(メッセージ樹 / 入力・添付 / params / 走行中タスク)と、
 * chat レコードの作成・保存(createNewChat / updateChatById)だけを持つ。
 * 送信側は send.ts(ChatSend)、受信側(イベント適用)は events.ts(ChatEvents)—
 * どちらもこのインスタンス経由で状態を共有する。ロード・新規初期化
 * (loadChat / initNewChat)は実行順の罠があるため Chat.svelte 側に残している。
 */
import { tick } from 'svelte';
import { get } from 'svelte/store';

import { chatId, chats, currentChatPage, type Model } from '$lib/stores';
import { createMessagesList } from '$lib/utils';
import { createNewChat, getChatList, updateChatById } from '$lib/apis/chats';
import type { ChatFile, ChatHistory } from '$lib/types/chat';

export type Translate = (key: string, options?: Record<string, unknown>) => string;

// chat 単位の params(chatContent.params 由来)。FE が読むキーのみ型付け
export type ChatParams = {
	system?: string;
	stream_response?: boolean;
	stop?: string | string[];
	[key: string]: unknown;
};

export class ChatSession {
	history = $state<ChatHistory>({
		messages: {},
		currentId: null
	});

	selectedModels = $state<string[]>(['']);
	atSelectedModel = $state<Model | undefined>(undefined);
	readonly selectedModelIds: string[] = $derived(
		this.atSelectedModel !== undefined ? [this.atSelectedModel.id] : this.selectedModels
	);

	// Chat Input
	prompt = $state('');
	chatFiles = $state<ChatFile[]>([]);
	files = $state<ChatFile[]>([]);

	params = $state<ChatParams>({});

	taskIds = $state<string[] | null>(null);

	autoScroll = $state(true);

	// 直近の create/update 応答(非リアクティブでよい)
	chat: any = null;

	#t: Translate;

	constructor(t: Translate) {
		this.#t = t;
	}

	saveSessionSelectedModels = () => {
		if (
			this.selectedModels.length === 0 ||
			(this.selectedModels.length === 1 && this.selectedModels[0] === '')
		) {
			return;
		}
		sessionStorage.selectedModels = JSON.stringify(this.selectedModels);
	};

	initChatHandler = async (history: ChatHistory) => {
		let _chatId = get(chatId);

		this.chat = await createNewChat({
			id: _chatId,
			title: this.#t('New Chat'),
			models: this.selectedModels,
			system: undefined,
			params: this.params,
			history: history,
			messages: createMessagesList(history, history.currentId),
			timestamp: Date.now()
		});

		_chatId = this.chat.id;
		await chatId.set(_chatId);

		await chats.set(await getChatList(get(currentChatPage)));
		currentChatPage.set(1);

		// upstream 由来: ここの `history` は引数の chat history(window.history ではない)を
		// 指しており state は常に undefined だった — 挙動を変えず undefined を明示する
		window.history.replaceState(undefined, '', `/c/${_chatId}`);
		await tick();

		return _chatId;
	};

	saveChatHandler = async (_chatId: string, history: ChatHistory) => {
		if (get(chatId) == _chatId) {
			this.chat = await updateChatById(_chatId, {
				models: this.selectedModels,
				history: history,
				messages: createMessagesList(history, history.currentId),
				params: this.params,
				files: this.chatFiles
			});
			currentChatPage.set(1);
			await chats.set(await getChatList(get(currentChatPage)));
		}
	};
}
