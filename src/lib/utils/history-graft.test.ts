// save-400 リカバリの接ぎ直し(#110 契約 §M6 / 批評 F-3)の契約。
// BE のサーバ駆動配達(通知 user + 空 assistant の直接 append)で stale になった
// ローカル history から、送信中の user+assistant 対をサーバ形の新 currentId へ
// parentId 接ぎ直しする。

import { describe, it, expect } from 'vitest';

import { graftLocalTurnOntoServerHistory, stripLocalTurn } from './history-graft';
import type { ChatHistory, ChatMessage } from '$lib/types/chat';

const msg = (
	id: string,
	role: string,
	parentId: string | null,
	childrenIds: string[] = [],
	extra: Partial<ChatMessage> = {}
): ChatMessage => ({
	id,
	parentId,
	childrenIds,
	role,
	content: `content-${id}`,
	...extra
});

const historyOf = (messages: ChatMessage[], currentId: string | null): ChatHistory => ({
	messages: Object.fromEntries(messages.map((m) => [m.id, m])),
	currentId
});

describe('graftLocalTurnOntoServerHistory', () => {
	it('送信中 user+assistant 対を BE 配達ターンの先端へ接ぎ直す(送信前保存の 400)', () => {
		// サーバ: u1 → a1 → notice(BE user) → be-a(BE 空 assistant)
		const server = historyOf(
			[
				msg('u1', 'user', null, ['a1']),
				msg('a1', 'assistant', 'u1', ['notice']),
				msg('notice', 'user', 'a1', ['be-a'], { meta: { system_notice: 'task' } }),
				msg('be-a', 'assistant', 'notice', [])
			],
			'be-a'
		);
		// ローカル: u1 → a1 → u2(送信中) → a2(応答受け皿)。notice を知らない
		const local = historyOf(
			[
				msg('u1', 'user', null, ['a1']),
				msg('a1', 'assistant', 'u1', ['u2']),
				msg('u2', 'user', 'a1', ['a2']),
				msg('a2', 'assistant', 'u2', [])
			],
			'a2'
		);

		const merged = graftLocalTurnOntoServerHistory(server, local);

		// 対は新 currentId(be-a)にぶら下がる
		expect(merged.messages['u2'].parentId).toBe('be-a');
		expect(merged.messages['be-a'].childrenIds).toEqual(['u2']);
		expect(merged.messages['a2'].parentId).toBe('u2');
		expect(merged.currentId).toBe('a2');
		// BE の通知子は温存され、旧位置(a1 の子)に u2 の幽霊参照を残さない
		expect(merged.messages['a1'].childrenIds).toEqual(['notice']);
		expect(merged.messages['notice'].meta?.system_notice).toBe('task');
	});

	it('末尾鎖が無い(全 id がサーバに在る)ときはサーバ先端を採り、本文はローカルで上書きする(完了時保存の 400)', () => {
		// サーバ: u1 → a1(保存時の途中本文) → notice → be-a
		const server = historyOf(
			[
				msg('u1', 'user', null, ['a1']),
				msg('a1', 'assistant', 'u1', ['notice'], { content: '' }),
				msg('notice', 'user', 'a1', ['be-a'], { meta: { system_notice: 'task' } }),
				msg('be-a', 'assistant', 'notice', [])
			],
			'be-a'
		);
		// ローカル: ストリーム済み本文を持つ(notice を知らない)
		const local = historyOf(
			[
				msg('u1', 'user', null, ['a1']),
				msg('a1', 'assistant', 'u1', [], { content: 'ストリーム済み本文', done: true })
			],
			'a1'
		);

		const merged = graftLocalTurnOntoServerHistory(server, local);

		expect(merged.messages['a1'].content).toBe('ストリーム済み本文');
		expect(merged.messages['a1'].done).toBe(true);
		// childrenIds はサーバ優先の和集合 — BE の通知子を消さない
		expect(merged.messages['a1'].childrenIds).toEqual(['notice']);
		// サーバの先端(配達ターン)が currentId
		expect(merged.currentId).toBe('be-a');
	});

	it('空チャットへの初回対(サーバ currentId = null)は root として接ぐ', () => {
		const server = historyOf([], null);
		const local = historyOf(
			[msg('u1', 'user', null, ['a1']), msg('a1', 'assistant', 'u1', [])],
			'a1'
		);

		const merged = graftLocalTurnOntoServerHistory(server, local);

		expect(merged.messages['u1'].parentId).toBeNull();
		expect(merged.messages['a1'].parentId).toBe('u1');
		expect(merged.currentId).toBe('a1');
	});

	it('入力は破壊しない(server / local とも)', () => {
		const server = historyOf([msg('u1', 'user', null, [])], 'u1');
		const local = historyOf(
			[msg('u1', 'user', null, ['a1']), msg('a1', 'assistant', 'u1', [])],
			'a1'
		);
		const serverSnapshot = JSON.parse(JSON.stringify(server));
		const localSnapshot = JSON.parse(JSON.stringify(local));

		graftLocalTurnOntoServerHistory(server, local);

		expect(server).toEqual(serverSnapshot);
		expect(local).toEqual(localSnapshot);
	});
});

// llens(#110): save-400 リカバリの refetch 失敗分岐の巻き戻し(敵対検証 A: 分岐非対称)。
// 送信対を残したまま ok:false → prompt 復元 → 再送で user が二重化するのを防ぐ。
describe('stripLocalTurn', () => {
	it('送信対(user+assistant)を取り除き、送信前の先端へ currentId を戻す', () => {
		// u1 → a1(保存済み)→ u2(送信中)→ a2(応答受け皿)
		const local = historyOf(
			[
				msg('u1', 'user', null, ['a1']),
				msg('a1', 'assistant', 'u1', ['u2']),
				msg('u2', 'user', 'a1', ['a2']),
				msg('a2', 'assistant', 'u2', [])
			],
			'a2'
		);

		const rolled = stripLocalTurn(local);

		expect(rolled.messages['u2']).toBeUndefined();
		expect(rolled.messages['a2']).toBeUndefined();
		expect(rolled.currentId).toBe('a1');
		// 幽霊参照を残さない
		expect(rolled.messages['a1'].childrenIds).toEqual([]);
	});

	it('再生成(既存 user に assistant を足しただけ)では保存済み user を残す', () => {
		// u1 → [a1(既存), a2(再生成の受け皿)]
		const local = historyOf(
			[
				msg('u1', 'user', null, ['a1', 'a2']),
				msg('a1', 'assistant', 'u1', []),
				msg('a2', 'assistant', 'u1', [])
			],
			'a2'
		);

		const rolled = stripLocalTurn(local);

		expect(rolled.messages['a2']).toBeUndefined();
		expect(rolled.messages['u1']).toBeDefined();
		expect(rolled.messages['u1'].childrenIds).toEqual(['a1']);
		expect(rolled.currentId).toBe('u1');
	});

	it('初回対(root からの送信)は空 history へ戻る', () => {
		const local = historyOf(
			[msg('u1', 'user', null, ['a1']), msg('a1', 'assistant', 'u1', [])],
			'a1'
		);

		const rolled = stripLocalTurn(local);

		expect(rolled.messages).toEqual({});
		expect(rolled.currentId).toBeNull();
	});

	it('currentId が null / 不在ならそのまま返し、入力は破壊しない', () => {
		const empty = historyOf([], null);
		expect(stripLocalTurn(empty)).toEqual(empty);

		const local = historyOf(
			[msg('u1', 'user', null, ['a1']), msg('a1', 'assistant', 'u1', [])],
			'a1'
		);
		const snapshot = JSON.parse(JSON.stringify(local));
		stripLocalTurn(local);
		expect(local).toEqual(snapshot);
	});
});
