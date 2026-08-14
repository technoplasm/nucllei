// llens(#110): saveChatHandler の save-400 リカバリ(契約 §M6 / 批評 F-3)の契約。
// 敵対検証 A(分岐非対称)の確定欠陥を縛る: refetch 失敗分岐も再保存失敗分岐と同じく
// ローカルの送信対を後始末してから ok:false を返す — 残すと呼び出し側の prompt 復元 →
// 再送で user メッセージが二重化する。

import { describe, it, expect, vi, beforeEach } from 'vitest';

// session.svelte の import チェーン($lib/stores → $app/* / $lib/utils の巨大依存)を
// test 環境から切る
vi.mock('$lib/stores', async () => {
	const { writable } = await import('svelte/store');
	return {
		chatId: writable('chat-1'),
		chats: writable([]),
		currentChatPage: writable(1)
	};
});
vi.mock('$lib/apis/chats', () => ({
	createNewChat: vi.fn(),
	getChatById: vi.fn(),
	getChatList: vi.fn(async () => []),
	updateChatById: vi.fn()
}));
vi.mock('$lib/utils', () => ({ createMessagesList: () => [] }));

import { chatId } from '$lib/stores';
import { getChatById, updateChatById } from '$lib/apis/chats';
import { ChatSession } from './session.svelte';
import type { ChatHistory } from '$lib/types/chat';

const msg = (
	id: string,
	role: string,
	parentId: string | null,
	childrenIds: string[] = [],
	extra: Record<string, unknown> = {}
) => ({ id, parentId, childrenIds, role, content: `content-${id}`, ...extra });

// u0(user) → a0(assistant, 保存済み先端) → u1(送信中 user) → r1(応答受け皿)
const localHistory = (): ChatHistory => ({
	messages: {
		u0: msg('u0', 'user', null, ['a0']),
		a0: msg('a0', 'assistant', 'u0', ['u1'], { done: true }),
		u1: msg('u1', 'user', 'a0', ['r1']),
		r1: msg('r1', 'assistant', 'u1', [])
	},
	currentId: 'r1'
});

// サーバ形: u0 → a0 → notice(BE 配達ターン) → be-a
const serverChat = () => ({
	chat: {
		history: {
			messages: {
				u0: msg('u0', 'user', null, ['a0']),
				a0: msg('a0', 'assistant', 'u0', ['notice'], { done: true }),
				notice: msg('notice', 'user', 'a0', ['be-a'], { meta: { system_notice: 'task' } }),
				'be-a': msg('be-a', 'assistant', 'notice', [])
			},
			currentId: 'be-a'
		}
	}
});

const makeSession = () => {
	const session = new ChatSession((key: string) => key);
	session.history = localHistory();
	return session;
};

beforeEach(() => {
	vi.clearAllMocks();
	chatId.set('chat-1');
});

describe('ChatSession.saveChatHandler(save-400 リカバリ)', () => {
	it('refetch まで失敗したら送信対を取り除いて巻き戻してから ok:false(分岐対称)', async () => {
		vi.mocked(updateChatById).mockRejectedValue({ status: 400 });
		vi.mocked(getChatById).mockRejectedValue(new Error('network down'));
		const session = makeSession();

		const res = await session.saveChatHandler('chat-1', localHistory());

		expect(res.ok).toBe(false);
		// 送信対(u1/r1)がローカルに残らない — 残ると prompt 復元 → 再送で user 二重化
		expect(session.history.messages['u1']).toBeUndefined();
		expect(session.history.messages['r1']).toBeUndefined();
		expect(session.history.currentId).toBe('a0');
		expect(session.history.messages['a0'].childrenIds).toEqual([]);
	});

	it('再保存も失敗したらサーバ形へ巻き戻してから ok:false(既存分岐の後始末)', async () => {
		vi.mocked(updateChatById).mockRejectedValue({ status: 400 });
		vi.mocked(getChatById).mockResolvedValue(serverChat());
		const session = makeSession();

		const res = await session.saveChatHandler('chat-1', localHistory());

		expect(res.ok).toBe(false);
		expect(session.history.messages['u1']).toBeUndefined();
		expect(session.history.currentId).toBe('be-a');
	});

	it('接ぎ直し再保存が通れば ok:true と merged history を返す(リカバリ成功系は不変)', async () => {
		vi.mocked(updateChatById)
			.mockRejectedValueOnce({ status: 400 })
			.mockResolvedValueOnce({ id: 'chat-1' });
		vi.mocked(getChatById).mockResolvedValue(serverChat());
		const session = makeSession();

		const res = await session.saveChatHandler('chat-1', localHistory());

		expect(res.ok).toBe(true);
		expect(res.history?.messages['u1'].parentId).toBe('be-a');
		expect(res.history?.currentId).toBe('r1');
		expect(session.history.messages['notice']).toBeDefined();
	});

	it('400 以外の save 失敗はリカバリせずそのまま throw', async () => {
		vi.mocked(updateChatById).mockRejectedValue({ status: 500 });
		const session = makeSession();

		await expect(session.saveChatHandler('chat-1', localHistory())).rejects.toEqual({
			status: 500
		});
		expect(getChatById).not.toHaveBeenCalled();
	});
});
