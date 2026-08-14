// 表示中 chat の exec 行キャッシュ(#110)の契約。
// 要: refreshAndScanExecutions への一本化 — fetch 成否に関わらず terminal 未回収の
// enqueue 走査を常に実行する(tick 述語ゲート内にだけ走査を置くと、リロード後・
// turn 末完了の自動再開が永久に回らない — 仕様 §9 の fe 批評修正)。
// fetch 失敗時は表示を現状維持しつつ lastRefreshFailed を立てる(poll の自己接地)。

import { describe, it, expect, beforeEach, vi } from 'vitest';

// executions store の import チェーン($lib/apis/chats → $lib/apis/fetch → $lib/stores
// → $app/*)を test 環境から切る
vi.mock('$lib/apis/chats', () => ({ getChatExecutions: vi.fn() }));

import { getChatExecutions } from '$lib/apis/chats';
import {
	clearExecutions,
	executionsState,
	getExecutionEntry,
	refreshAndScanExecutions,
	type ExecutionEntry
} from './executions.svelte';
import {
	buildExecutionResumeMessage,
	executionResumeState,
	resetExecutionResume
} from './execution-resume.svelte';
import type { ChatHistory } from '$lib/types/chat';

const CHAT = 'chat-1';
const EMPTY_HISTORY: ChatHistory = { messages: {}, currentId: null };

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
	...extra
});

const mockedGetChatExecutions = vi.mocked(getChatExecutions);

beforeEach(() => {
	clearExecutions();
	resetExecutionResume();
	mockedGetChatExecutions.mockReset();
});

describe('refreshAndScanExecutions', () => {
	it('終端判定: 4 状態のみ enqueue。running・未知の文字列は終端扱いしない(誤 enqueue 防止)', async () => {
		mockedGetChatExecutions.mockResolvedValue([
			entry('exec-done', 'done'),
			entry('exec-failed', 'failed'),
			entry('exec-stopped', 'stopped'),
			entry('exec-orphaned', 'orphaned'),
			entry('exec-running', 'running'),
			entry('exec-queued', 'queued')
		]);

		await refreshAndScanExecutions(CHAT, EMPTY_HISTORY);

		expect(executionsState.unrecoveredIds).toEqual([
			'exec-done',
			'exec-failed',
			'exec-stopped',
			'exec-orphaned'
		]);
	});

	it('fetch 成功: entries を差し替え、terminal 未回収を enqueue + unrecoveredIds に載せる', async () => {
		mockedGetChatExecutions.mockResolvedValue([
			entry('exec-1', 'running'),
			entry('exec-2', 'done', { finished_at: 1700000100, duration: 42.5 })
		]);

		await refreshAndScanExecutions(CHAT, EMPTY_HISTORY);

		expect(executionsState.chatId).toBe(CHAT);
		expect(executionsState.entries).toHaveLength(2);
		expect(executionsState.lastRefreshFailed).toBe(false);
		expect(executionsState.unrecoveredIds).toEqual(['exec-2']);
		expect(executionResumeState.entries).toEqual([
			{ chatId: CHAT, execId: 'exec-2', toolName: 'run_subagent', status: 'done' }
		]);
		expect(getExecutionEntry('exec-1')?.status).toBe('running');
	});

	it('履歴で処理済み(定型文に full exec_id)の terminal は enqueue しない', async () => {
		mockedGetChatExecutions.mockResolvedValue([entry('exec-2', 'done')]);
		const handledHistory: ChatHistory = {
			messages: {
				u1: {
					id: 'u1',
					parentId: null,
					childrenIds: [],
					role: 'user',
					content: buildExecutionResumeMessage([
						{ chatId: CHAT, execId: 'exec-2', toolName: 'run_subagent', status: 'done' }
					])
				}
			},
			currentId: 'u1'
		};

		await refreshAndScanExecutions(CHAT, handledHistory);

		expect(executionsState.unrecoveredIds).toEqual([]);
		expect(executionResumeState.entries).toHaveLength(0);
	});

	it('fetch 失敗: 表示は現状維持 + lastRefreshFailed を立て、保持中 entries で走査は実行する', async () => {
		mockedGetChatExecutions.mockResolvedValue([entry('exec-2', 'done')]);
		await refreshAndScanExecutions(CHAT, EMPTY_HISTORY);
		resetExecutionResume(); // enqueue 済みを消し、失敗 tick でも走査が回ることを見る

		mockedGetChatExecutions.mockRejectedValue(new Error('network down'));
		await refreshAndScanExecutions(CHAT, EMPTY_HISTORY);

		expect(executionsState.entries).toHaveLength(1); // ちらつかせない
		expect(executionsState.lastRefreshFailed).toBe(true);
		expect(executionsState.unrecoveredIds).toEqual(['exec-2']);
		expect(executionResumeState.entries.map((r) => r.execId)).toEqual(['exec-2']);
	});

	it('fetch 失敗 + chat 切替: 前 chat の entries では走査しない(不明・空として扱う)', async () => {
		mockedGetChatExecutions.mockResolvedValue([entry('exec-1', 'done')]);
		await refreshAndScanExecutions(CHAT, EMPTY_HISTORY);
		resetExecutionResume();

		mockedGetChatExecutions.mockRejectedValue(new Error('network down'));
		await refreshAndScanExecutions('chat-other', EMPTY_HISTORY);

		expect(executionsState.chatId).toBe('chat-other');
		expect(executionsState.entries).toEqual([]);
		expect(executionsState.lastRefreshFailed).toBe(true);
		expect(executionResumeState.entries).toHaveLength(0);
	});

	it('clearExecutions は in-flight の fetch を無効化する(seq ガード)', async () => {
		let resolve!: (value: ExecutionEntry[]) => void;
		mockedGetChatExecutions.mockReturnValue(
			new Promise<ExecutionEntry[]>((r) => {
				resolve = r;
			})
		);
		const pending = refreshAndScanExecutions(CHAT, EMPTY_HISTORY);
		clearExecutions();
		resolve([entry('exec-1', 'done')]);
		await pending;

		expect(executionsState.chatId).toBeNull();
		expect(executionsState.entries).toEqual([]);
		expect(executionResumeState.entries).toHaveLength(0);
	});

	it('空 chatId は no-op', async () => {
		await refreshAndScanExecutions('', EMPTY_HISTORY);
		expect(mockedGetChatExecutions).not.toHaveBeenCalled();
	});
});
