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
import { createNewChat, getChatById, getChatList, updateChatById } from '$lib/apis/chats';
import { graftLocalTurnOntoServerHistory, stripLocalTurn } from '$lib/utils/history-graft';
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

	/**
	 * llens(#110): 保存 + save-400 リカバリ(契約 §M6 / 批評 F-3 裁定)。
	 *
	 * BE のサーバ駆動配達(通知ターンの直接 append)と競合すると updateChatById が
	 * 400 で拒否する。その場合は ① getChatById で refetch ② ローカルの送信中
	 * user+assistant 対を新 currentId へ接ぎ直し(history-graft)③ 再保存 1 回。
	 * それでも失敗なら {ok:false} — 呼び出し側(send.ts)が prompt 復元 + toast。
	 * 接ぎ直しが起きたときは history に採用した merged を返す(送信側は wire を
	 * これで組み直す — 通知メッセージがモデルに載る)。
	 */
	saveChatHandler = async (
		_chatId: string,
		history: ChatHistory
	): Promise<{ ok: boolean; history?: ChatHistory }> => {
		if (get(chatId) != _chatId) {
			return { ok: true };
		}
		const save = (target: ChatHistory) =>
			updateChatById(_chatId, {
				models: this.selectedModels,
				history: target,
				messages: createMessagesList(target, target.currentId),
				params: this.params,
				files: this.chatFiles
			});

		let saved: unknown = null;
		let merged: ChatHistory | undefined;
		try {
			saved = await save(history);
		} catch (e) {
			if ((e as { status?: number } | null)?.status !== 400) {
				throw e;
			}
			const server = await getChatById(_chatId).catch(() => null);
			if (!server?.chat?.history) {
				// refetch まで失敗 — 再保存失敗分岐と後始末を揃える: 送信対を取り除いた形へ
				// 巻き戻す(残すと prompt 復元 → 再送で user メッセージが二重化する)
				this.history = stripLocalTurn(history);
				return { ok: false };
			}
			merged = graftLocalTurnOntoServerHistory(server.chat.history, history);
			try {
				saved = await save(merged);
			} catch (e2) {
				// 再試行は 1 回だけ。ローカルは refetch したサーバ形へ揃える
				// (未保存の見かけを残さない — 呼び出し側が prompt を入力欄へ復元する)
				this.history = server.chat.history;
				return { ok: false };
			}
			this.history = merged;
		}

		this.chat = saved;
		currentChatPage.set(1);
		await chats.set(await getChatList(get(currentChatPage)));
		return { ok: true, ...(merged ? { history: merged } : {}) };
	};
}
