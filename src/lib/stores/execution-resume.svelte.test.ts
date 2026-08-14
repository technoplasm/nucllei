// execution(#110)の親ターン自動再開キューの契約。
// 要: exec 単位 once ガード(無限ループ防止の第一段)、定型文への full exec_id 同梱
// (handled 判定の要)、isExecutionResumeHandled の決定性(timeout の running 形結果は
// 未処理扱い — K5 の「時間切れ → FE 自動再開が後で回収」を塞がない)、
// 連鎖上限 countTrailingAutoResumes の履歴由来の決定性。

import { describe, it, expect, beforeEach } from 'vitest';

import type { ChatHistory } from '$lib/types/chat';
import {
	buildExecutionResumeMessage,
	clearExecutionResumeChainLimited,
	countTrailingAutoResumes,
	enqueueExecutionResume,
	executionResumeState,
	hasPendingExecutionResume,
	isExecutionResumeChainLimited,
	isExecutionResumeHandled,
	isExecutionResumeMessage,
	requeueExecutionResumes,
	resetExecutionResume,
	setExecutionResumeChainLimited,
	takeExecutionResumes,
	type ExecutionResumeRequest
} from './execution-resume.svelte';

const CHAT = 'chat-1';
const OTHER_CHAT = 'chat-2';
const EXEC = 'exec-aaaa-1111';

const request = (
	execId = EXEC,
	status: ExecutionResumeRequest['status'] = 'done',
	toolName = 'run_subagent'
) => ({ execId, toolName, status });

// モジュール $state と once ガード(requested 集合)はテスト間で生き残る — 毎回リセット
beforeEach(() => {
	resetExecutionResume();
});

describe('enqueue / take / requeue', () => {
	it('要求を積み、take は指定 chat の全件を消費する(バッチ 1 通送信の形)', () => {
		enqueueExecutionResume(CHAT, request(EXEC));
		enqueueExecutionResume(CHAT, request('exec-bbbb-2222', 'failed'));
		enqueueExecutionResume(OTHER_CHAT, request('exec-cccc-3333'));

		expect(hasPendingExecutionResume(CHAT)).toBe(true);
		const taken = takeExecutionResumes(CHAT);
		expect(taken.map((r) => r.execId)).toEqual([EXEC, 'exec-bbbb-2222']);
		expect(hasPendingExecutionResume(CHAT)).toBe(false);
		// 他 chat の要求は残す
		expect(hasPendingExecutionResume(OTHER_CHAT)).toBe(true);
		expect(takeExecutionResumes(CHAT)).toEqual([]);
	});

	it('exec 単位 once: 同じ exec の再検知は積まれない(take で消費した後も)', () => {
		enqueueExecutionResume(CHAT, request());
		enqueueExecutionResume(CHAT, request());
		expect(executionResumeState.entries).toHaveLength(1);

		takeExecutionResumes(CHAT);
		// 消費後の再検知(ポーリング走査は毎 tick 回る)でも再 enqueue しない
		enqueueExecutionResume(CHAT, request());
		expect(executionResumeState.entries).toHaveLength(0);
	});

	it('chatId / execId 欠落の要求は捨てる(once ガードも汚さない)', () => {
		enqueueExecutionResume('', request());
		enqueueExecutionResume(CHAT, request(''));
		expect(executionResumeState.entries).toHaveLength(0);
		enqueueExecutionResume(CHAT, request());
		expect(executionResumeState.entries).toHaveLength(1);
	});

	it('requeue は once ガードを通さず直接戻す(送信失敗時の経路)', () => {
		enqueueExecutionResume(CHAT, request());
		const taken = takeExecutionResumes(CHAT);
		requeueExecutionResumes(taken);
		expect(hasPendingExecutionResume(CHAT)).toBe(true);
		expect(takeExecutionResumes(CHAT)).toHaveLength(1);
	});
});

describe('chain limited フラグ(連鎖上限のチップ表示)', () => {
	it('chat 単位で立てて消す。他 chat には見えない', () => {
		expect(isExecutionResumeChainLimited(CHAT)).toBe(false);
		setExecutionResumeChainLimited(CHAT);
		expect(isExecutionResumeChainLimited(CHAT)).toBe(true);
		expect(isExecutionResumeChainLimited(OTHER_CHAT)).toBe(false);
		clearExecutionResumeChainLimited();
		expect(isExecutionResumeChainLimited(CHAT)).toBe(false);
	});
});

describe('buildExecutionResumeMessage / isExecutionResumeMessage', () => {
	it('バッチ 1 通に全 exec の full exec_id を含める(handled 判定の要)', () => {
		const message = buildExecutionResumeMessage([
			{ chatId: CHAT, ...request(EXEC, 'done') },
			{ chatId: CHAT, ...request('exec-bbbb-2222', 'failed', 'run_python') }
		]);
		expect(message).toContain(EXEC);
		expect(message).toContain('exec-bbbb-2222');
		expect(message).toContain('run_python');
		expect(message).toContain('await_async');
		expect(isExecutionResumeMessage(message)).toBe(true);
	});

	it('status 別の文言: stopped は停止の報告、orphaned は喪失の報告(回収を促さない)', () => {
		const stopped = buildExecutionResumeMessage([{ chatId: CHAT, ...request(EXEC, 'stopped') }]);
		expect(stopped).toContain('停止');
		const orphaned = buildExecutionResumeMessage([{ chatId: CHAT, ...request(EXEC, 'orphaned') }]);
		expect(orphaned).toContain('失われました');
		expect(orphaned).not.toContain('回収し、続行');
	});

	it('通常のユーザー発話は定型文ではない', () => {
		expect(isExecutionResumeMessage('こんにちは')).toBe(false);
		expect(isExecutionResumeMessage('')).toBe(false);
		expect(isExecutionResumeMessage(null)).toBe(false);
	});
});

// parentId 鎖の線形履歴を組み立てる(currentId = 末尾)
const chainHistory = (messages: { id: string; role: string; content: string }[]): ChatHistory => {
	const record: ChatHistory['messages'] = {};
	messages.forEach((m, i) => {
		record[m.id] = {
			id: m.id,
			parentId: i > 0 ? messages[i - 1].id : null,
			childrenIds: i < messages.length - 1 ? [messages[i + 1].id] : [],
			role: m.role,
			content: m.content
		};
	});
	return { messages: record, currentId: messages.at(-1)?.id ?? null };
};

describe('countTrailingAutoResumes(連鎖上限の判定)', () => {
	const resume = (execId: string) =>
		buildExecutionResumeMessage([{ chatId: CHAT, ...request(execId) }]);

	it('履歴末尾から連続する自動 turn を数え、人間の発話で打ち切る', () => {
		const history = chainHistory([
			{ id: 'u1', role: 'user', content: '調べて' },
			{ id: 'a1', role: 'assistant', content: 'spawn しました' },
			{ id: 'u2', role: 'user', content: resume('exec-1') },
			{ id: 'a2', role: 'assistant', content: '回収しました' },
			{ id: 'u3', role: 'user', content: resume('exec-2') },
			{ id: 'a3', role: 'assistant', content: '回収しました' }
		]);
		expect(countTrailingAutoResumes(history)).toBe(2);
	});

	it('末尾に人間の発話が入ると 0 に戻る(発話で連鎖が切れる)', () => {
		const history = chainHistory([
			{ id: 'u1', role: 'user', content: resume('exec-1') },
			{ id: 'a1', role: 'assistant', content: '回収しました' },
			{ id: 'u2', role: 'user', content: 'ありがとう、続けて' },
			{ id: 'a2', role: 'assistant', content: 'はい' }
		]);
		expect(countTrailingAutoResumes(history)).toBe(0);
	});

	it('空履歴・currentId 無しは 0', () => {
		expect(countTrailingAutoResumes({ messages: {}, currentId: null })).toBe(0);
	});
});

describe('isExecutionResumeHandled', () => {
	const historyWith = (messages: ChatHistory['messages']): ChatHistory => ({
		messages,
		currentId: Object.keys(messages).at(-1) ?? null
	});

	it('full exec_id を含む定型 resume メッセージが履歴にあれば処理済み', () => {
		const history = historyWith({
			u1: {
				id: 'u1',
				parentId: null,
				childrenIds: [],
				role: 'user',
				content: buildExecutionResumeMessage([{ chatId: CHAT, ...request(EXEC) }])
			}
		});
		expect(isExecutionResumeHandled(history, EXEC)).toBe(true);
		expect(isExecutionResumeHandled(history, 'exec-other')).toBe(false);
	});

	it('exec_id が本文に含まれても定型文でない user 発話は処理済みにしない', () => {
		const history = historyWith({
			u1: {
				id: 'u1',
				parentId: null,
				childrenIds: [],
				role: 'user',
				content: `この ${EXEC} ってなに?`
			}
		});
		expect(isExecutionResumeHandled(history, EXEC)).toBe(false);
	});

	it('await_async call + 終端の回収結果があれば処理済み', () => {
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
						calls: [
							{
								id: 'call-1',
								function: { name: 'await_async', arguments: JSON.stringify({ exec_id: EXEC }) }
							}
						],
						results: [
							{
								tool_call_id: 'call-1',
								content: JSON.stringify({ ok: true, exec_id: EXEC, status: 'done', result: 'x' })
							}
						]
					}
				]
			}
		});
		expect(isExecutionResumeHandled(history, EXEC)).toBe(true);
	});

	it('await_async の timeout(running 形)結果は未処理 — 後の自動再開を塞がない(K5)', () => {
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
						calls: [
							{
								id: 'call-1',
								function: { name: 'await_async', arguments: JSON.stringify({ exec_id: EXEC }) }
							}
						],
						results: [
							{
								tool_call_id: 'call-1',
								content: JSON.stringify({ exec_id: EXEC, status: 'running', elapsed_s: 90 })
							}
						]
					}
				]
			}
		});
		expect(isExecutionResumeHandled(history, EXEC)).toBe(false);
	});

	it('await_async call に結果がまだ無い・他 tool の call は未処理', () => {
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
						calls: [
							{
								id: 'call-1',
								function: { name: 'await_async', arguments: JSON.stringify({ exec_id: EXEC }) }
							},
							{
								id: 'call-2',
								function: { name: 'check_async', arguments: JSON.stringify({ exec_id: EXEC }) }
							}
						],
						results: [{ tool_call_id: 'call-2', content: JSON.stringify({ status: 'done' }) }]
					}
				]
			}
		});
		expect(isExecutionResumeHandled(history, EXEC)).toBe(false);
	});

	it('空履歴では false(誤 drop しない)・空 execId は true(番兵)', () => {
		expect(isExecutionResumeHandled({ messages: {}, currentId: null }, EXEC)).toBe(false);
		expect(isExecutionResumeHandled({ messages: {}, currentId: null }, '')).toBe(true);
	});
});
