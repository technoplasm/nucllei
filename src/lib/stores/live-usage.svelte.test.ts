import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
	clearLiveUsage,
	formatTokens,
	getActiveLiveUsage,
	getChatUsageBaseline,
	getLastTurnRate,
	getLiveUsage,
	resetChatUsage,
	setChatUsageBaseline,
	setLiveUsage
} from './live-usage.svelte';

// モジュール $state はテスト間で生き残る — 各テストの前に全部リセットする。
// 時間は fake timers で固定(実装は Date.now を読む)。
beforeEach(() => {
	resetChatUsage();
	vi.useFakeTimers();
	vi.setSystemTime(1_000_000);
});

afterEach(() => {
	vi.useRealTimers();
});

describe('setLiveUsage', () => {
	it('最初のサンプルで startedAt が確定し、累積値がそのまま入る', () => {
		setLiveUsage('m1', {
			prompt_tokens: 100,
			completion_tokens: 10,
			total_tokens: 110,
			context_size: 262144
		});

		const live = getLiveUsage('m1');
		expect(live).not.toBeNull();
		expect(live?.startedAt).toBe(1_000_000);
		expect(live?.prompt_tokens).toBe(100);
		expect(live?.completion_tokens).toBe(10);
		expect(live?.total_tokens).toBe(110);
		expect(live?.context_size).toBe(262144);
		// 1 サンプルではレートは出ない
		expect(live?.rate).toBeNull();
	});

	it('後続サンプルで正の tok/s(EMA)が出る', () => {
		setLiveUsage('m1', { prompt_tokens: 100, completion_tokens: 0, total_tokens: 100 });

		vi.advanceTimersByTime(1000);
		setLiveUsage('m1', { prompt_tokens: 100, completion_tokens: 30, total_tokens: 130 });
		// 初回は瞬間値で seed: (30 - 0) / 1s = 30
		expect(getLiveUsage('m1')?.rate).toBeCloseTo(30);

		vi.advanceTimersByTime(1000);
		setLiveUsage('m1', { prompt_tokens: 100, completion_tokens: 90, total_tokens: 190 });
		// EMA(α=0.3): 30 + 0.3 * (60 - 30) = 39
		const rate = getLiveUsage('m1')?.rate;
		expect(rate).toBeGreaterThan(0);
		expect(rate).toBeCloseTo(39);
	});

	it('completion の巻き戻りは新セグメント: レートが負にならず累計に積まれる', () => {
		setLiveUsage('m1', { prompt_tokens: 100, completion_tokens: 40, total_tokens: 140 });
		vi.advanceTimersByTime(1000);
		setLiveUsage('m1', { prompt_tokens: 100, completion_tokens: 100, total_tokens: 200 });
		expect(getLiveUsage('m1')?.rate).toBeCloseTo(60);

		// tool ループの次のモデル呼び出し — completion が小さい値へ巻き戻る
		vi.advanceTimersByTime(500);
		setLiveUsage('m1', { prompt_tokens: 220, completion_tokens: 5, total_tokens: 225 });

		const live = getLiveUsage('m1');
		expect(live?.completion_tokens).toBe(5);
		// EMA は維持(負のレートで壊れない)
		expect(live?.rate).toBeCloseTo(60);

		// レート計算の基準もリセットされている: 次の増分から正常に計算される
		vi.advanceTimersByTime(1000);
		setLiveUsage('m1', { prompt_tokens: 220, completion_tokens: 55, total_tokens: 275 });
		// instant = (55 - 5) / 1s = 50 → 60 + 0.3 * (50 - 60) = 57
		expect(getLiveUsage('m1')?.rate).toBeCloseTo(57);
	});
});

describe('clearLiveUsage', () => {
	it('破棄時にターン平均 tok/s(累計 completion / 実測経過秒)が lastTurnRate に残る', () => {
		setLiveUsage('m1', { prompt_tokens: 100, completion_tokens: 40, total_tokens: 140 });
		vi.advanceTimersByTime(1000);
		setLiveUsage('m1', { prompt_tokens: 100, completion_tokens: 100, total_tokens: 200 });
		vi.advanceTimersByTime(500);
		// 巻き戻り(前セグメントの 100 が累計へ)
		setLiveUsage('m1', { prompt_tokens: 220, completion_tokens: 5, total_tokens: 225 });
		vi.advanceTimersByTime(1000);
		setLiveUsage('m1', { prompt_tokens: 220, completion_tokens: 55, total_tokens: 275 });

		clearLiveUsage('m1');

		expect(getLiveUsage('m1')).toBeNull();
		// 累計 completion = 100 + 55 = 155、実測経過 = 2.5s → 62 tok/s
		expect(getLastTurnRate()).toBeCloseTo(62);
	});

	it('サンプル 1 件(経過ゼロ)の破棄では lastTurnRate を触らない', () => {
		setLiveUsage('m1', { prompt_tokens: 100, completion_tokens: 10, total_tokens: 110 });
		clearLiveUsage('m1');
		expect(getLastTurnRate()).toBeNull();
	});
});

describe('getActiveLiveUsage', () => {
	it('直近サンプルのエントリを返す', () => {
		setLiveUsage('m1', { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 });
		vi.advanceTimersByTime(1000);
		setLiveUsage('m2', { prompt_tokens: 10, completion_tokens: 10, total_tokens: 20 });

		expect(getActiveLiveUsage()?.total_tokens).toBe(20);

		clearLiveUsage('m2');
		expect(getActiveLiveUsage()?.total_tokens).toBe(2);

		clearLiveUsage('m1');
		expect(getActiveLiveUsage()).toBeNull();
	});
});

describe('chat usage baseline', () => {
	it('Chat.meta.usage / chat:completion の usage を seed できる', () => {
		setChatUsageBaseline({
			prompt_tokens: 41267,
			completion_tokens: 1200,
			total_tokens: 42467,
			context_size: 262144
		});

		expect(getChatUsageBaseline()).toEqual({
			prompt_tokens: 41267,
			completion_tokens: 1200,
			total_tokens: 42467,
			context_size: 262144
		});
	});

	it('null で消え、数値フィールドの無い形は据え置き', () => {
		setChatUsageBaseline({ prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 });
		setChatUsageBaseline({ note: 'not-a-usage' });
		expect(getChatUsageBaseline()?.prompt_tokens).toBe(10);

		setChatUsageBaseline(null);
		expect(getChatUsageBaseline()).toBeNull();
	});

	it('resetChatUsage で baseline / lastTurnRate / live エントリが全部消える', () => {
		setChatUsageBaseline({ prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 });
		setLiveUsage('m1', { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 });
		vi.advanceTimersByTime(1000);
		setLiveUsage('m1', { prompt_tokens: 1, completion_tokens: 11, total_tokens: 12 });
		clearLiveUsage('m1');
		expect(getLastTurnRate()).not.toBeNull();

		setLiveUsage('m2', { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 });
		resetChatUsage();

		expect(getChatUsageBaseline()).toBeNull();
		expect(getLastTurnRate()).toBeNull();
		expect(getActiveLiveUsage()).toBeNull();
	});
});

describe('formatTokens', () => {
	it('1024 基数で k / M に畳む(llens token_meter と同じ見え方)', () => {
		expect(formatTokens(0)).toBe('0');
		expect(formatTokens(1023)).toBe('1023');
		expect(formatTokens(1024)).toBe('1.0k');
		expect(formatTokens(41267)).toBe('40.3k');
		expect(formatTokens(262144)).toBe('256.0k');
		expect(formatTokens(1048576)).toBe('1.0M');
		expect(formatTokens(1572864)).toBe('1.5M');
	});

	it('不正値は 0 に落とす', () => {
		expect(formatTokens(-1)).toBe('0');
		expect(formatTokens(Number.NaN)).toBe('0');
	});
});
