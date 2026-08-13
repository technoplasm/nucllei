// background subagent の親ターン自動再開キュー(#109)の契約。
// 要: session 単位 once ガード(無限ループ防止の第一段)と、
// リロード後の再検知を drop する isSubagentResumeHandled の決定性。

import { describe, it, expect, beforeEach } from 'vitest';

import type { ChatHistory } from '$lib/types/chat';
import {
	buildSubagentResumeMessage,
	isSubagentResumeHandled,
	isSubagentResumeRequested,
	listRunningSubagentSpawns,
	peekSubagentResume,
	requestSubagentResume,
	resetSubagentResume,
	subagentResumeState,
	takeSubagentResume
} from './subagent-resume.svelte';

const PARENT = 'parent-chat-1';
const OTHER_PARENT = 'parent-chat-2';
const SESSION = 'child-session-1';

// モジュール $state と once ガード(requested 集合)はテスト間で生き残る — 毎回リセット
beforeEach(() => {
	resetSubagentResume();
});

describe('requestSubagentResume / peek / take', () => {
	it('要求を積み、peek は消費せず、take は先頭 1 件を消費する', () => {
		requestSubagentResume(PARENT, SESSION);

		expect(subagentResumeState.entries).toEqual([{ parentChatId: PARENT, sessionId: SESSION }]);
		expect(peekSubagentResume(PARENT)).toEqual({ parentChatId: PARENT, sessionId: SESSION });
		expect(subagentResumeState.entries).toHaveLength(1);

		expect(takeSubagentResume(PARENT)).toEqual({ parentChatId: PARENT, sessionId: SESSION });
		expect(subagentResumeState.entries).toHaveLength(0);
		expect(takeSubagentResume(PARENT)).toBeNull();
	});

	it('session 単位 once: 同じ session の再要求は積まれない(take で消費した後も)', () => {
		requestSubagentResume(PARENT, SESSION);
		requestSubagentResume(PARENT, SESSION);
		expect(subagentResumeState.entries).toHaveLength(1);

		takeSubagentResume(PARENT);
		// 消費後の再検知(live done → マウント時 done 等)でも再 enqueue しない —
		// 自動送信は session につき最大 1 回(ページセッション内)
		requestSubagentResume(PARENT, SESSION);
		expect(subagentResumeState.entries).toHaveLength(0);
	});

	it('take は指定親 chat の要求だけ消費し、他 chat の要求は残す(FIFO)', () => {
		requestSubagentResume(OTHER_PARENT, 'other-session');
		requestSubagentResume(PARENT, SESSION);
		requestSubagentResume(PARENT, 'child-session-2');

		expect(takeSubagentResume(PARENT)?.sessionId).toBe(SESSION);
		expect(takeSubagentResume(PARENT)?.sessionId).toBe('child-session-2');
		expect(takeSubagentResume(PARENT)).toBeNull();
		expect(peekSubagentResume(OTHER_PARENT)?.sessionId).toBe('other-session');
	});

	it('parentChatId / sessionId 欠落の要求は捨てる', () => {
		requestSubagentResume('', SESSION);
		requestSubagentResume(PARENT, '');
		expect(subagentResumeState.entries).toHaveLength(0);
		// '' で once ガードを汚していない(正規の要求は通る)
		requestSubagentResume(PARENT, SESSION);
		expect(subagentResumeState.entries).toHaveLength(1);
	});

	it('isSubagentResumeRequested: enqueue で立ち、take 後も立ったまま(once の可視化)', () => {
		expect(isSubagentResumeRequested(SESSION)).toBe(false);
		requestSubagentResume(PARENT, SESSION);
		expect(isSubagentResumeRequested(SESSION)).toBe(true);
		takeSubagentResume(PARENT);
		// 消費後も requested のまま — 常時検知ポーリングが再照会しないための契約
		expect(isSubagentResumeRequested(SESSION)).toBe(true);
	});
});

describe('buildSubagentResumeMessage', () => {
	it('session id を含む定型文(モデルへの機械指示 — ja 固定)', () => {
		const message = buildSubagentResumeMessage(SESSION);
		expect(message).toContain(SESSION);
		expect(message).toContain('await_subagent');
	});
});

describe('isSubagentResumeHandled', () => {
	const historyWith = (messages: ChatHistory['messages']): ChatHistory => ({
		messages,
		currentId: Object.keys(messages).at(-1) ?? null
	});

	it('定型 resume メッセージが履歴にあれば処理済み(自動送信済みの再検知を drop)', () => {
		const history = historyWith({
			u1: {
				id: 'u1',
				parentId: null,
				childrenIds: [],
				role: 'user',
				content: buildSubagentResumeMessage(SESSION)
			}
		});
		expect(isSubagentResumeHandled(history, SESSION)).toBe(true);
		expect(isSubagentResumeHandled(history, 'other-session')).toBe(false);
	});

	it('answer 付き subagent tool result が履歴にあれば処理済み(回収済みの再検知を drop)', () => {
		const history = historyWith({
			a1: {
				id: 'a1',
				parentId: null,
				childrenIds: [],
				role: 'assistant',
				content: '',
				content_blocks: [
					{
						type: 'tool_calls',
						results: [
							{
								tool_call_id: 'functions.await_subagent:0',
								content: JSON.stringify({ ok: true, session_id: SESSION, answer: '結果です。' })
							}
						]
					}
				]
			}
		});
		expect(isSubagentResumeHandled(history, SESSION)).toBe(true);
	});

	it('spawn 即返し形(status: running・answer 無し)だけの履歴は未処理', () => {
		const history = historyWith({
			a1: {
				id: 'a1',
				parentId: null,
				childrenIds: [],
				role: 'assistant',
				content: '',
				content_blocks: [
					{
						type: 'tool_calls',
						results: [
							{
								tool_call_id: 'functions.run_subagent:0',
								content: JSON.stringify({ ok: true, session_id: SESSION, status: 'running' })
							}
						]
					}
				]
			}
		});
		expect(isSubagentResumeHandled(history, SESSION)).toBe(false);
	});

	it('壊れた result(非 JSON・型違い)や空履歴では false(誤 drop しない)', () => {
		expect(isSubagentResumeHandled({ messages: {}, currentId: null }, SESSION)).toBe(false);
		const history = historyWith({
			a1: {
				id: 'a1',
				parentId: null,
				childrenIds: [],
				role: 'assistant',
				content: '',
				content_blocks: [
					{
						type: 'tool_calls',
						results: [{ tool_call_id: 't1', content: `boom ${SESSION} not-json` }]
					}
				]
			}
		});
		expect(isSubagentResumeHandled(history, SESSION)).toBe(false);
	});
});

describe('listRunningSubagentSpawns', () => {
	const historyWith = (messages: ChatHistory['messages']): ChatHistory => ({
		messages,
		currentId: Object.keys(messages).at(-1) ?? null
	});

	it('running 形 spawn 結果の session_id を重複なしで抽出する', () => {
		const runningResult = (sessionId: string) => ({
			tool_call_id: `functions.run_subagent:${sessionId}`,
			content: JSON.stringify({ ok: true, session_id: sessionId, status: 'running' })
		});
		const history = historyWith({
			a1: {
				id: 'a1',
				parentId: null,
				childrenIds: ['a2'],
				role: 'assistant',
				content: '',
				content_blocks: [
					{ type: 'tool_calls', results: [runningResult(SESSION), runningResult('child-2')] }
				]
			},
			a2: {
				id: 'a2',
				parentId: 'a1',
				childrenIds: [],
				role: 'assistant',
				content: '',
				// 同じ session の再表示(regenerate 等)— 重複させない
				content_blocks: [{ type: 'tool_calls', results: [runningResult(SESSION)] }]
			}
		});
		expect(listRunningSubagentSpawns(history)).toEqual([SESSION, 'child-2']);
	});

	it('answer 形・running 以外・非 JSON・text ブロックは対象外', () => {
		const history = historyWith({
			a1: {
				id: 'a1',
				parentId: null,
				childrenIds: [],
				role: 'assistant',
				content: 'running と本文に書いてあるだけ',
				content_blocks: [
					{ type: 'text', content: 'status running の話' },
					{
						type: 'tool_calls',
						results: [
							{
								tool_call_id: 't1',
								// answer 形(回収済み)— running ではない
								content: JSON.stringify({ ok: true, session_id: SESSION, answer: '結果' })
							},
							{
								tool_call_id: 't2',
								// answer と status が同居する形は即返し形ではない
								content: JSON.stringify({
									ok: true,
									session_id: 'child-2',
									answer: '結果',
									status: 'running'
								})
							},
							{ tool_call_id: 't3', content: 'running not-json' },
							{
								tool_call_id: 't4',
								// session_id 欠落
								content: JSON.stringify({ ok: true, status: 'running' })
							}
						]
					}
				]
			}
		});
		expect(listRunningSubagentSpawns(history)).toEqual([]);
	});

	it('空履歴では空配列', () => {
		expect(listRunningSubagentSpawns({ messages: {}, currentId: null })).toEqual([]);
	});
});
