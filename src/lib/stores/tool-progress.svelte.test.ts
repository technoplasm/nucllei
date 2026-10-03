import { describe, it, expect, beforeEach } from 'vitest';
import {
	clearChatProgress,
	clearMessageProgress,
	getToolProgress,
	setToolProgress
} from './tool-progress.svelte';

// store は chatId をエントリに持つ(#107 W-A)。テストでは c1 / c2 の 2 チャットだけ使う。
const C1 = 'c1';
const C2 = 'c2';

// モジュール $state はテスト間で生き残る — 各テストの前に使用スコープを全部リセットする。
beforeEach(() => {
	clearChatProgress(C1);
	clearChatProgress(C2);
});

describe('setToolProgress / getToolProgress', () => {
	it('message + tool_call 単位で記録し、同じ tool_call_id は上書きする', () => {
		setToolProgress(C1, 'm1', { tool_call_id: 't1', description: 'step 1' });
		setToolProgress(C1, 'm1', { tool_call_id: 't1', description: 'step 2', current: 1, total: 3 });

		const progress = getToolProgress('m1', 't1');
		expect(progress?.description).toBe('step 2');
		expect(progress?.current).toBe(1);
		expect(progress?.chatId).toBe(C1);
	});

	it('chatId 無し・tool_call_id 無しの通知は捨てる(宛先が作れず掃除もできない)', () => {
		setToolProgress('', 'm1', { tool_call_id: 't1', description: 'orphan' });
		expect(getToolProgress('m1', 't1')).toBeNull();

		setToolProgress(C1, 'm1', { description: 'no call id' });
		expect(getToolProgress('m1', '')).toBeNull();
	});
});

describe('clearMessageProgress', () => {
	it('メッセージ単位で捨てる(他メッセージは残る)', () => {
		setToolProgress(C1, 'm1', { tool_call_id: 't1', description: 'a' });
		setToolProgress(C1, 'm2', { tool_call_id: 't1', description: 'b' });

		clearMessageProgress('m1');

		expect(getToolProgress('m1', 't1')).toBeNull();
		expect(getToolProgress('m2', 't1')?.description).toBe('b');
	});
});

describe('clearChatProgress', () => {
	it('chat 単位で一括破棄し、他 chat のエントリは消さない', () => {
		setToolProgress(C1, 'm1', { tool_call_id: 't1', description: 'a' });
		setToolProgress(C1, 'm2', { tool_call_id: 't2', description: 'b' });
		setToolProgress(C2, 'm9', { tool_call_id: 't1', description: 'other chat' });

		clearChatProgress(C1);

		expect(getToolProgress('m1', 't1')).toBeNull();
		expect(getToolProgress('m2', 't2')).toBeNull();
		expect(getToolProgress('m9', 't1')?.description).toBe('other chat');
	});
});

// #140 UI 追従: run_subagent の子 session の所在は粘着(task_id は後着で埋まる)
describe('subagent の所在', () => {
	it('session_id → task_id の順に届いた所在を合成し、所在の無い後続通知でも保持する', () => {
		setToolProgress(C1, 'm1', {
			tool_call_id: 't1',
			description: 'agent session s1',
			subagent: { session_id: 's1' }
		});
		expect(getToolProgress('m1', 't1')?.subagent).toEqual({ session_id: 's1' });

		setToolProgress(C1, 'm1', {
			tool_call_id: 't1',
			subagent: { session_id: 's1', task_id: 'task-1' }
		});
		setToolProgress(C1, 'm1', { tool_call_id: 't1', done: true });

		const progress = getToolProgress('m1', 't1');
		expect(progress?.subagent).toEqual({ session_id: 's1', task_id: 'task-1' });
		expect(progress?.done).toBe(true);
	});

	it('所在を持たない tool には付かない', () => {
		setToolProgress(C1, 'm1', { tool_call_id: 't2', description: 'x' });
		expect(getToolProgress('m1', 't2')?.subagent).toBeUndefined();
	});
});
