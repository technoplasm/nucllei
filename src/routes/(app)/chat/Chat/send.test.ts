// llens(#102): floating ゲージの「計測中」は送信の瞬間から出す(最初の chat:completion を
// 待つと新規チャットで ~80ms 空になる)。印の付け外しを ChatSend.sendPrompt で縛る:
// - 新規チャットは chat 作成前('' スコープ)から付き、作成後は実 id へ付け替わる
// - 失敗・中断の各分岐で必ず外れる(スピナーが残らない)

import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('$lib/stores', async () => {
	const { writable } = await import('svelte/store');
	return {
		chatId: writable(''),
		chats: writable([]),
		config: writable({}),
		currentChatPage: writable(1),
		models: writable([{ id: 'm', name: 'M', info: {} }]),
		settings: writable({ params: {} }),
		user: writable({ name: 'u' })
	};
});
vi.mock('svelte-sonner', () => ({ toast: { error: vi.fn() } }));
vi.mock('$lib/utils', () => ({
	createMessagesList: () => [],
	promptTemplate: (s: string) => s,
	getPromptVariables: () => ({})
}));
vi.mock('$lib/utils/chat-messages', () => ({
	buildWireMessages: () => [],
	parseStopSequences: () => undefined
}));
vi.mock('$lib/utils/completion-streams', () => ({
	consumeCompletionStream: vi.fn(() => new Promise(() => {})),
	getActiveTaskIds: () => []
}));
vi.mock('$lib/apis/chats', () => ({ getChatList: vi.fn(async () => []) }));
vi.mock('$lib/apis/openai', () => ({ generateOpenAIChatCompletion: vi.fn() }));
vi.mock('$lib/apis', () => ({ stopTask: vi.fn() }));
vi.mock('$lib/constants', () => ({ WEBUI_BASE_URL: '' }));
vi.mock('./session.svelte', () => ({}));
vi.mock('./events', () => ({}));

import { chatId } from '$lib/stores';
import { generateOpenAIChatCompletion } from '$lib/apis/openai';
import { consumeCompletionStream } from '$lib/utils/completion-streams';
import { isLiveUsagePending, resetChatUsage } from '$lib/stores/live-usage.svelte';
import { ChatSend } from './send';
import type { ChatHistory } from '$lib/types/chat';

const NEW_ID = 'chat-new';

const makeSend = ({
	saveOk = true,
	onStreamLost
}: { saveOk?: boolean; onStreamLost?: (chatId: string, messageId: string) => void } = {}) => {
	const seen: Record<string, boolean[]> = { init: [] };
	const session = {
		autoScroll: false,
		selectedModelIds: ['m'],
		selectedModels: ['m'],
		params: {},
		prompt: '',
		chatFiles: [],
		taskIds: null as string[] | null,
		history: {
			messages: { u1: { id: 'u1', parentId: null, childrenIds: [], role: 'user', content: 'hi' } },
			currentId: 'u1'
		} as unknown as ChatHistory,
		initChatHandler: vi.fn(async () => {
			// chat 作成中(POST /chats/new 待ち)— 開始画面の $chatId('')でもう計測中
			seen.init.push(isLiveUsagePending(''));
			chatId.set(NEW_ID);
			return NEW_ID;
		}),
		saveChatHandler: vi.fn(async () => ({ ok: saveOk }))
	};
	const events = { chatCompletedHandler: vi.fn(async () => {}) };
	const send = new ChatSend({
		session: session as never,
		events: events as never,
		t: (k: string) => k,
		eventTarget: new EventTarget(),
		scrollToBottom: () => {},
		onStreamLost
	});
	const sendNew = () =>
		send.sendPrompt(session.history, 'hi', 'u1', { newChat: true }) as Promise<void>;
	return { send, session, events, seen, sendNew };
};

beforeEach(() => {
	vi.clearAllMocks();
	vi.spyOn(console, 'error').mockImplementation(() => {}); // 失敗分岐の想定内ログ
	vi.spyOn(console, 'warn').mockImplementation(() => {});
	chatId.set('');
	resetChatUsage('');
	resetChatUsage(NEW_ID);
});

describe('ChatSend.sendPrompt — 送信時の usage pending(#102)', () => {
	it('chat 作成前から付き、作成後は実 id へ付け替わる(応答待ちの間も残る)', async () => {
		vi.mocked(generateOpenAIChatCompletion).mockImplementation(async () => {
			expect(isLiveUsagePending(NEW_ID)).toBe(true);
			expect(isLiveUsagePending('')).toBe(false);
			return { body: {} } as never;
		});
		const { seen, sendNew } = makeSend();

		await sendNew();

		expect(seen.init).toEqual([true]);
		expect(consumeCompletionStream).toHaveBeenCalled();
		// 外すのは最初の chat:usage / done(events 側)— ここでは残っている
		expect(isLiveUsagePending(NEW_ID)).toBe(true);
		expect(isLiveUsagePending('')).toBe(false);
	});

	it('completion リクエストの失敗で外れる', async () => {
		vi.mocked(generateOpenAIChatCompletion).mockRejectedValue(new Error('boom'));
		const { events, sendNew } = makeSend();

		await sendNew();

		expect(events.chatCompletedHandler).toHaveBeenCalled();
		expect(isLiveUsagePending(NEW_ID)).toBe(false);
	});

	it('task 生成前の stop(fetch abort)で外れる', async () => {
		const { send, sendNew } = makeSend();
		vi.mocked(generateOpenAIChatCompletion).mockImplementation(
			(_body, _url, signal?: AbortSignal | null) =>
				new Promise((_resolve, reject) => {
					signal?.addEventListener('abort', () => reject(new Error('aborted')));
				}) as never
		);

		const pending = sendNew();
		await vi.waitFor(() => expect(generateOpenAIChatCompletion).toHaveBeenCalled());
		expect(isLiveUsagePending(NEW_ID)).toBe(true);
		await send.stopResponse();
		await pending;

		expect(isLiveUsagePending(NEW_ID)).toBe(false);
	});

	it('保存失敗(送信中止)で外れる', async () => {
		const { sendNew } = makeSend({ saveOk: false });

		await sendNew();

		expect(generateOpenAIChatCompletion).not.toHaveBeenCalled();
		expect(isLiveUsagePending(NEW_ID)).toBe(false);
	});

	it('chat 作成の失敗で開始画面の印も外れる', async () => {
		const { session, sendNew } = makeSend();
		session.initChatHandler.mockRejectedValueOnce(new Error('create failed'));

		await expect(sendNew()).rejects.toThrow('create failed');

		expect(isLiveUsagePending('')).toBe(false);
	});
});

// ken の実機(モバイル)で、サーバーは正常に完走したターンに
// error: {content: 'TypeError: network error'} が保存された。SSE の切断は応答の失敗では
// ない — message.error に書かない(書くと再アタッチ後の done で completed → 保存に乗る)
describe('ChatSend.sendPrompt — SSE 消費中の切断', () => {
	const assistantOf = (session: ReturnType<typeof makeSend>['session']) =>
		Object.values(session.history.messages).find((m) => m.role === 'assistant')!;

	const disconnectAfter = (mutate: (session: ReturnType<typeof makeSend>['session']) => void) => {
		const lost = vi.fn();
		const ctx = makeSend({ onStreamLost: lost });
		vi.mocked(generateOpenAIChatCompletion).mockResolvedValue({ body: {} } as never);
		vi.mocked(consumeCompletionStream).mockImplementationOnce(async () => {
			// 受信済みの本文(イベント適用はライブの history に入る)
			mutate(ctx.session);
			throw new TypeError('network error');
		});
		return { ...ctx, lost };
	};

	it('done 受信後の切断は error にせず、受信済みの本文も消さない', async () => {
		const { session, sendNew, lost } = disconnectAfter((session) => {
			const m = assistantOf(session);
			m.content = 'answer';
			m.done = true;
		});

		await sendNew();
		await vi.waitFor(() => expect(console.warn).toHaveBeenCalled());

		const message = assistantOf(session);
		expect(message.error).toBeUndefined();
		expect(message.content).toBe('answer');
		expect(message.done).toBe(true);
		expect(lost).not.toHaveBeenCalled();
	});

	it('done 前の切断は error にせず未完のまま置き、追いつきを依頼する', async () => {
		const { session, sendNew, lost, events } = disconnectAfter((session) => {
			assistantOf(session).content = 'partial';
		});

		await sendNew();
		await vi.waitFor(() => expect(lost).toHaveBeenCalled());

		const message = assistantOf(session);
		expect(lost).toHaveBeenCalledWith(NEW_ID, message.id);
		expect(message.error).toBeUndefined();
		expect(message.content).toBe('partial');
		expect(message.done).not.toBe(true);
		// completed(outlet・保存)はここからは駆動しない — 追いつき後の done が担う
		expect(events.chatCompletedHandler).not.toHaveBeenCalled();
		expect(isLiveUsagePending(NEW_ID)).toBe(false);
	});
});
