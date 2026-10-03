// 表示中 chat の exec 行キャッシュ(#110)の契約。
// 自動再開はサーバ駆動(契約 T3)— FE は履歴走査・enqueue を持たない。
// 未回収 = API の delivered フィールド由来(terminal ∧ !delivered)。
// poll 述語(shouldPollExecutions)は cadence 制御のみ:
// lastRefreshFailed ∨ 自タブ taskIds ∨ running ∨ 未配達 terminal ∨
// (terminal ∧ delivered ∧ finished_at が直近 10 分)。

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// executions store の import チェーン($lib/apis/chats → $lib/apis/fetch → $lib/stores
// → $app/*)を test 環境から切る
vi.mock('$lib/apis/chats', () => ({ getChatExecutions: vi.fn() }));

import { getChatExecutions } from '$lib/apis/chats';
import {
	applyExecutionDone,
	clearExecutions,
	executionsState,
	EXECUTION_POLL_MS,
	getExecutionEntry,
	isExecutionsRefreshBackedOff,
	isUndeliveredTerminal,
	refreshExecutions,
	refreshExecutionsUntilTerminal,
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

describe('activeOnly(B-6 — 有界応答のマージ)', () => {
	it('既知行を上書き・新規を末尾追加し、応答に無い終端行を保持する', async () => {
		// seed(全量)
		respond([
			entry('old-1', 'done', { delivered: true, finished_at: NOW - 3600 }),
			entry('run-1', 'running')
		]);
		await refreshExecutions(CHAT);

		// tick(active): old-1 は応答に無い / run-1 は done へ / new-1 が出現
		respond([entry('run-1', 'done', { finished_at: NOW }), entry('new-1', 'running')]);
		await refreshExecutions(CHAT, { activeOnly: true });

		expect(mockedGetChatExecutions).toHaveBeenLastCalledWith(CHAT, 'active');
		expect(executionsState.entries.map((e) => e.id)).toEqual(['old-1', 'run-1', 'new-1']);
		// 応答に無い終端行の保持 — ExecutionBlock の状態表示が running に退行しない
		expect(getExecutionEntry('old-1')?.status).toBe('done');
		expect(getExecutionEntry('run-1')?.status).toBe('done');
	});

	it('activeOnly でも delivered 増分は検知される', async () => {
		respond([entry('run-1', 'running')]);
		await refreshExecutions(CHAT);

		respond([entry('run-1', 'done', { delivered: true, finished_at: NOW })]);
		expect((await refreshExecutions(CHAT, { activeOnly: true })).deliveredAdvanced).toBe(true);
	});

	it('非最終と見ていた行が active 応答から消えたら全量で照合し直す(>追従窓の休止の追いつき)', async () => {
		respond([entry('run-1', 'running')]);
		await refreshExecutions(CHAT);

		// 休止中に終端化・配達され追従窓も過ぎた → active には無い → 全量 refetch
		mockedGetChatExecutions.mockResolvedValueOnce({ executions: [] });
		mockedGetChatExecutions.mockResolvedValueOnce({
			executions: [entry('run-1', 'done', { delivered: true, finished_at: NOW - 1200 })]
		});
		const { deliveredAdvanced } = await refreshExecutions(CHAT, { activeOnly: true });

		expect(mockedGetChatExecutions).toHaveBeenLastCalledWith(CHAT, undefined);
		expect(getExecutionEntry('run-1')?.status).toBe('done'); // stale running が残らない
		expect(deliveredAdvanced).toBe(true); // 追いつき refetch(history 読み直し)の合図
	});

	it('chat 不一致(seed 前)の activeOnly は差し替えとして扱う', async () => {
		respond([entry('a-1', 'running')]);
		await refreshExecutions(CHAT);

		respond([entry('b-1', 'running')]);
		await refreshExecutions('chat-other', { activeOnly: true });

		expect(executionsState.chatId).toBe('chat-other');
		expect(executionsState.entries.map((e) => e.id)).toEqual(['b-1']);
	});
});

describe('isExecutionsRefreshBackedOff(B-2 — 連続失敗の backoff)', () => {
	const T0 = 1_700_000_000_000; // ms

	const failOnce = async () => {
		mockedGetChatExecutions.mockRejectedValueOnce(new Error('network down'));
		await refreshExecutions(CHAT);
	};

	let nowSpy: { mockRestore(): void };

	beforeEach(() => {
		nowSpy = vi.spyOn(Date, 'now').mockReturnValue(T0);
	});

	afterEach(() => {
		nowSpy.mockRestore();
	});

	it('失敗 1 回は基本間隔のまま(次 tick は通る)、2 回目から倍々', async () => {
		await failOnce();
		expect(isExecutionsRefreshBackedOff(T0 + EXECUTION_POLL_MS - 1)).toBe(true);
		expect(isExecutionsRefreshBackedOff(T0 + EXECUTION_POLL_MS)).toBe(false);

		await failOnce(); // 2 回目 → 10s
		expect(isExecutionsRefreshBackedOff(T0 + EXECUTION_POLL_MS)).toBe(true);
		expect(isExecutionsRefreshBackedOff(T0 + 2 * EXECUTION_POLL_MS)).toBe(false);
	});

	it('上限 60s で頭打ち', async () => {
		for (let i = 0; i < 8; i++) {
			await failOnce();
		}
		expect(isExecutionsRefreshBackedOff(T0 + 60_000 - 1)).toBe(true);
		expect(isExecutionsRefreshBackedOff(T0 + 60_000)).toBe(false);
	});

	it('成功で即解除(以後の失敗は 1 回目から数え直し)', async () => {
		await failOnce();
		await failOnce();
		respond([entry('exec-1', 'running')]);
		await refreshExecutions(CHAT);
		expect(isExecutionsRefreshBackedOff(T0)).toBe(false);

		await failOnce(); // 数え直し → 5s
		expect(isExecutionsRefreshBackedOff(T0 + EXECUTION_POLL_MS)).toBe(false);
	});

	it('clearExecutions でリセット', async () => {
		await failOnce();
		await failOnce();
		clearExecutions();
		expect(isExecutionsRefreshBackedOff(T0)).toBe(false);
	});
});

describe('deliveredAdvanced(復帰後の追いつき合図)', () => {
	it('同一 chat で delivered が増えたときだけ true(初回観測は false)', async () => {
		respond([entry('exec-1', 'running')]);
		expect((await refreshExecutions(CHAT)).deliveredAdvanced).toBe(false);

		// 変化なし
		respond([entry('exec-1', 'running')]);
		expect((await refreshExecutions(CHAT)).deliveredAdvanced).toBe(false);

		// 非表示休止中に配達が完走 → 復帰後の観測で delivered へ遷移
		respond([entry('exec-1', 'done', { delivered: true, finished_at: NOW })]);
		expect((await refreshExecutions(CHAT)).deliveredAdvanced).toBe(true);

		// 以後は安定(再度 true にならない)
		respond([entry('exec-1', 'done', { delivered: true, finished_at: NOW })]);
		expect((await refreshExecutions(CHAT)).deliveredAdvanced).toBe(false);
	});

	it('chat 切替直後の初回観測は delivered 済みでも false(履歴は fetch 済み)', async () => {
		respond([entry('exec-1', 'done', { delivered: true, finished_at: NOW })]);
		expect((await refreshExecutions(CHAT)).deliveredAdvanced).toBe(false);

		clearExecutions();
		respond([entry('exec-9', 'done', { delivered: true, finished_at: NOW })]);
		expect((await refreshExecutions('chat-2')).deliveredAdvanced).toBe(false);
	});

	it('fetch 失敗を挟んでも基準は保持される(復帰成功時に増分を検知)', async () => {
		respond([entry('exec-1', 'running')]);
		await refreshExecutions(CHAT);

		mockedGetChatExecutions.mockRejectedValueOnce(new Error('network'));
		expect((await refreshExecutions(CHAT)).deliveredAdvanced).toBe(false);

		respond([entry('exec-1', 'done', { delivered: true, finished_at: NOW })]);
		expect((await refreshExecutions(CHAT)).deliveredAdvanced).toBe(true);
	});
});

// #140 UI 追従: 終端は BE の `execution:done` push で即反映し、stream が無いときは
// 子の完走観測を合図に短い再試行で読み直す(5 秒ポーリングを待たない)
describe('applyExecutionDone', () => {
	it('表示中 chat の既知行を終端へ即時に書き換える(delivered は触らない)', async () => {
		respond([entry('exec-1', 'running')]);
		await refreshExecutions(CHAT);

		const applied = applyExecutionDone(CHAT, {
			task_id: 'exec-1',
			status: 'done',
			finished_at: NOW,
			duration: 3.2
		});
		expect(applied).toBe(true);
		const updated = getExecutionEntry('exec-1');
		expect(updated?.status).toBe('done');
		expect(updated?.finished_at).toBe(NOW);
		expect(updated?.duration).toBe(3.2);
		expect(updated?.delivered).toBe(false);
		expect(isUndeliveredTerminal(updated!)).toBe(true);
	});

	it('他 chat・未知行・非終端 status は無視する', async () => {
		respond([entry('exec-1', 'running')]);
		await refreshExecutions(CHAT);

		expect(applyExecutionDone('chat-2', { task_id: 'exec-1', status: 'done' })).toBe(false);
		expect(applyExecutionDone(CHAT, { task_id: 'exec-x', status: 'done' })).toBe(false);
		expect(applyExecutionDone(CHAT, { task_id: 'exec-1', status: 'running' })).toBe(false);
		expect(applyExecutionDone(CHAT, { task_id: 'exec-1' })).toBe(false);
		expect(getExecutionEntry('exec-1')?.status).toBe('running');
	});
});

describe('refreshExecutionsUntilTerminal', () => {
	const noSleep = () => Promise.resolve();

	it('終端が見えるまで短く再試行して読み直す', async () => {
		respond([entry('exec-1', 'running')]);
		await refreshExecutions(CHAT);

		// 1 回目はまだ running(exec 行の終端書きは子の完走より僅かに遅れる)→ 2 回目で done
		mockedGetChatExecutions
			.mockResolvedValueOnce({ executions: [entry('exec-1', 'running')] })
			.mockResolvedValueOnce({
				executions: [entry('exec-1', 'done', { finished_at: NOW })]
			});
		expect(await refreshExecutionsUntilTerminal('exec-1', noSleep)).toBe(true);
		expect(getExecutionEntry('exec-1')?.status).toBe('done');
		// 1 (seed) + 2 (再試行)
		expect(mockedGetChatExecutions).toHaveBeenCalledTimes(3);
	});

	it('既に終端(push 済み)なら読み直さない', async () => {
		respond([entry('exec-1', 'running')]);
		await refreshExecutions(CHAT);
		applyExecutionDone(CHAT, { task_id: 'exec-1', status: 'done' });

		expect(await refreshExecutionsUntilTerminal('exec-1', noSleep)).toBe(true);
		expect(mockedGetChatExecutions).toHaveBeenCalledTimes(1);
	});

	it('表示中 chat が無ければ何もしない', async () => {
		expect(await refreshExecutionsUntilTerminal('exec-1', noSleep)).toBe(false);
		expect(mockedGetChatExecutions).not.toHaveBeenCalled();
	});
});
