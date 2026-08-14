// 表示中 chat の exec 行キャッシュ(#110)の契約。
// 自動再開はサーバ駆動(契約 T3)— FE は履歴走査・enqueue を持たない。
// 未回収 = API の delivered フィールド由来(terminal ∧ !delivered)。
// poll 述語(shouldPollExecutions)は cadence 制御のみ:
// lastRefreshFailed ∨ 自タブ taskIds ∨ running ∨ 未配達 terminal ∨
// (terminal ∧ delivered ∧ finished_at が直近 10 分)。

import { describe, it, expect, beforeEach, vi } from 'vitest';

// executions store の import チェーン($lib/apis/chats → $lib/apis/fetch → $lib/stores
// → $app/*)を test 環境から切る
vi.mock('$lib/apis/chats', () => ({ getChatExecutions: vi.fn() }));

import { getChatExecutions } from '$lib/apis/chats';
import {
	clearExecutions,
	executionsState,
	getExecutionEntry,
	isUndeliveredTerminal,
	refreshExecutions,
	shouldPollExecutions,
	type ExecutionEntry
} from './executions.svelte';

const CHAT = 'chat-1';
const NOW = 1700000600; // epoch 秒(finished_at 起点の窓判定に使う)

const entry = (
	id: string,
	status: string,
	extra: Partial<ExecutionEntry> = {}
): ExecutionEntry => ({
	id,
	tool_name: 'run_subagent',
	status,
	created_at: 1700000000,
	finished_at: null,
	duration: null,
	error: null,
	delivered: false,
	...extra
});

const mockedGetChatExecutions = vi.mocked(getChatExecutions);

const respond = (executions: ExecutionEntry[], resume_suspended?: boolean) => {
	mockedGetChatExecutions.mockResolvedValue({ executions, resume_suspended });
};

beforeEach(() => {
	clearExecutions();
	mockedGetChatExecutions.mockReset();
});

describe('refreshExecutions', () => {
	it('fetch 成功: entries / resume_suspended を差し替える', async () => {
		respond(
			[entry('exec-1', 'running'), entry('exec-2', 'done', { finished_at: NOW, duration: 42.5 })],
			true
		);

		await refreshExecutions(CHAT);

		expect(executionsState.chatId).toBe(CHAT);
		expect(executionsState.entries).toHaveLength(2);
		expect(executionsState.resumeSuspended).toBe(true);
		expect(executionsState.lastRefreshFailed).toBe(false);
		expect(getExecutionEntry('exec-1')?.status).toBe('running');
	});

	it('resume_suspended 不在(BE 追随前)は false として扱う(undefined 耐性)', async () => {
		respond([entry('exec-1', 'running')]);

		await refreshExecutions(CHAT);

		expect(executionsState.resumeSuspended).toBe(false);
	});

	it('fetch 失敗: 表示は現状維持 + lastRefreshFailed を立てる(poll の自己接地)', async () => {
		respond([entry('exec-2', 'done')]);
		await refreshExecutions(CHAT);

		mockedGetChatExecutions.mockRejectedValue(new Error('network down'));
		await refreshExecutions(CHAT);

		expect(executionsState.entries).toHaveLength(1); // ちらつかせない
		expect(executionsState.lastRefreshFailed).toBe(true);
	});

	it('fetch 失敗 + chat 切替: 前 chat の entries を見せない(不明・空として扱う)', async () => {
		respond([entry('exec-1', 'done')], true);
		await refreshExecutions(CHAT);

		mockedGetChatExecutions.mockRejectedValue(new Error('network down'));
		await refreshExecutions('chat-other');

		expect(executionsState.chatId).toBe('chat-other');
		expect(executionsState.entries).toEqual([]);
		expect(executionsState.resumeSuspended).toBe(false);
		expect(executionsState.lastRefreshFailed).toBe(true);
	});

	it('clearExecutions は in-flight の fetch を無効化する(seq ガード)', async () => {
		let resolve!: (value: { executions: ExecutionEntry[] }) => void;
		mockedGetChatExecutions.mockReturnValue(
			new Promise<{ executions: ExecutionEntry[] }>((r) => {
				resolve = r;
			})
		);
		const pending = refreshExecutions(CHAT);
		clearExecutions();
		resolve({ executions: [entry('exec-1', 'done')] });
		await pending;

		expect(executionsState.chatId).toBeNull();
		expect(executionsState.entries).toEqual([]);
	});

	it('空 chatId は no-op', async () => {
		await refreshExecutions('');
		expect(mockedGetChatExecutions).not.toHaveBeenCalled();
	});
});

describe('isUndeliveredTerminal', () => {
	it('terminal 4 状態 ∧ !delivered のみ真。running・未知 status・配達済みは偽', () => {
		for (const status of ['done', 'failed', 'stopped', 'orphaned']) {
			expect(isUndeliveredTerminal(entry('x', status))).toBe(true);
			expect(isUndeliveredTerminal(entry('x', status, { delivered: true }))).toBe(false);
		}
		expect(isUndeliveredTerminal(entry('x', 'running'))).toBe(false);
		expect(isUndeliveredTerminal(entry('x', 'queued'))).toBe(false);
	});
});

describe('shouldPollExecutions(poll 述語 — 契約 §M6 / 批評 F-2)', () => {
	it('running / 未配達 terminal で真、全配達済み(古い)で偽', async () => {
		respond([entry('exec-1', 'running')]);
		await refreshExecutions(CHAT);
		expect(shouldPollExecutions(CHAT, false, NOW)).toBe(true);

		respond([entry('exec-2', 'done')]); // 未配達 terminal
		await refreshExecutions(CHAT);
		expect(shouldPollExecutions(CHAT, false, NOW)).toBe(true);

		// 配達済み ∧ finished_at が 10 分より古い → 偽
		respond([entry('exec-3', 'done', { delivered: true, finished_at: NOW - 601 })]);
		await refreshExecutions(CHAT);
		expect(shouldPollExecutions(CHAT, false, NOW)).toBe(false);
	});

	it('配達済み terminal でも finished_at が直近 10 分なら真(BE 発ターンの発見窓)', async () => {
		respond([entry('exec-1', 'done', { delivered: true, finished_at: NOW - 30 })]);
		await refreshExecutions(CHAT);
		expect(shouldPollExecutions(CHAT, false, NOW)).toBe(true);
	});

	it('自タブ taskIds / 直近 refresh 失敗は entries と無関係に真(自己接地)', async () => {
		expect(shouldPollExecutions(CHAT, true, NOW)).toBe(true);

		mockedGetChatExecutions.mockRejectedValue(new Error('network down'));
		await refreshExecutions(CHAT);
		expect(shouldPollExecutions(CHAT, false, NOW)).toBe(true);
	});

	it('他 chat の entries では真にならない(chatId スコープ)', async () => {
		respond([entry('exec-1', 'running')]);
		await refreshExecutions(CHAT);
		expect(shouldPollExecutions('chat-other', false, NOW)).toBe(false);
	});
});
