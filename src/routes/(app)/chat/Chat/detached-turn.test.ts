// done 前に SSE が切れた自ターンの追いつき判定(detached-turn.ts)。
// 再訪(loadChat)と同じ二択: 走行中なら再アタッチして done を待つ、終わっていれば読み直す

import { describe, it, expect, vi } from 'vitest';

import { resyncDetachedTurn, type DetachedTurnDeps } from './detached-turn';

const TURN = { chatId: 'c1', messageId: 'a1' };

const makeDeps = (over: Partial<DetachedTurnDeps> = {}): DetachedTurnDeps => ({
	currentChatId: () => 'c1',
	isDone: () => false,
	attachTasks: vi.fn(async () => ['t1']),
	reloadHistory: vi.fn(async () => {}),
	...over
});

describe('resyncDetachedTurn', () => {
	it('タスクが生きていればアタッチし、done 到着まで保留を続ける', async () => {
		const deps = makeDeps();

		expect(await resyncDetachedTurn(TURN, deps)).toBe(false);
		expect(deps.attachTasks).toHaveBeenCalledWith('c1');
		expect(deps.reloadHistory).not.toHaveBeenCalled();
	});

	it('再アタッチ経由で done が届いていれば片付く(I/O しない)', async () => {
		const deps = makeDeps({ isDone: () => true });

		expect(await resyncDetachedTurn(TURN, deps)).toBe(true);
		expect(deps.attachTasks).not.toHaveBeenCalled();
	});

	it('タスクが終わっていればサーバーの保存済み history を読み直す', async () => {
		const deps = makeDeps({ attachTasks: vi.fn(async () => []) });

		expect(await resyncDetachedTurn(TURN, deps)).toBe(true);
		expect(deps.reloadHistory).toHaveBeenCalledWith('c1');
	});

	it('task 一覧の取得失敗は保留のまま(次の機会に再試行)', async () => {
		const deps = makeDeps({ attachTasks: vi.fn(async () => null) });

		expect(await resyncDetachedTurn(TURN, deps)).toBe(false);
		expect(deps.reloadHistory).not.toHaveBeenCalled();
	});

	it('別チャットへ移っていれば何もせず片付く(再訪の loadChat が担う)', async () => {
		const deps = makeDeps({ currentChatId: () => 'other' });

		expect(await resyncDetachedTurn(TURN, deps)).toBe(true);
		expect(deps.attachTasks).not.toHaveBeenCalled();
		expect(deps.reloadHistory).not.toHaveBeenCalled();
	});
});
