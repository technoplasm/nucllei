import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
	chipUsage,
	clearLiveUsage,
	contextUsageFromUsage,
	formatTokens,
	getActiveLiveUsage,
	getChatUsageBaseline,
	getLiveUsage,
	isLiveUsagePending,
	markLiveUsagePending,
	resetChatUsage,
	setChatUsageBaseline,
	setLiveUsage,
	turnStatsFromUsage
} from './live-usage.svelte';

// store は chatId スコープ(#107 W-A)。テストでは c1 / c2 の 2 チャットだけ使う。
const C1 = 'c1';
const C2 = 'c2';

// モジュール $state はテスト間で生き残る — 各テストの前に使用スコープを全部リセットする。
// 時間は fake timers で固定(実装は Date.now を読む)。
beforeEach(() => {
	resetChatUsage(C1);
	resetChatUsage(C2);
	vi.useFakeTimers();
	vi.setSystemTime(1_000_000);
});

afterEach(() => {
	vi.useRealTimers();
});

describe('setLiveUsage', () => {
	it('最初のサンプルで startedAt が確定し、累積値がそのまま入る', () => {
		setLiveUsage(C1, 'm1', {
			prompt_tokens: 100,
			completion_tokens: 10,
			total_tokens: 110,
			context_size: 262144
		});

		const live = getLiveUsage(C1, 'm1');
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
		setLiveUsage(C1, 'm1', { prompt_tokens: 100, completion_tokens: 0, total_tokens: 100 });

		vi.advanceTimersByTime(1000);
		setLiveUsage(C1, 'm1', { prompt_tokens: 100, completion_tokens: 30, total_tokens: 130 });
		// 初回は瞬間値で seed: (30 - 0) / 1s = 30
		expect(getLiveUsage(C1, 'm1')?.rate).toBeCloseTo(30);

		vi.advanceTimersByTime(1000);
		setLiveUsage(C1, 'm1', { prompt_tokens: 100, completion_tokens: 90, total_tokens: 190 });
		// EMA(α=0.3): 30 + 0.3 * (60 - 30) = 39
		const rate = getLiveUsage(C1, 'm1')?.rate;
		expect(rate).toBeGreaterThan(0);
		expect(rate).toBeCloseTo(39);
	});

	it('completion の巻き戻りは新セグメント: レートが負にならず累計に積まれる', () => {
		setLiveUsage(C1, 'm1', { prompt_tokens: 100, completion_tokens: 40, total_tokens: 140 });
		vi.advanceTimersByTime(1000);
		setLiveUsage(C1, 'm1', { prompt_tokens: 100, completion_tokens: 100, total_tokens: 200 });
		expect(getLiveUsage(C1, 'm1')?.rate).toBeCloseTo(60);

		// tool ループの次のモデル呼び出し — completion が小さい値へ巻き戻る
		vi.advanceTimersByTime(500);
		setLiveUsage(C1, 'm1', { prompt_tokens: 220, completion_tokens: 5, total_tokens: 225 });

		const live = getLiveUsage(C1, 'm1');
		expect(live?.completion_tokens).toBe(5);
		// EMA は維持(負のレートで壊れない)
		expect(live?.rate).toBeCloseTo(60);

		// レート計算の基準もリセットされている: 次の増分から正常に計算される
		vi.advanceTimersByTime(1000);
		setLiveUsage(C1, 'm1', { prompt_tokens: 220, completion_tokens: 55, total_tokens: 275 });
		// instant = (55 - 5) / 1s = 50 → 60 + 0.3 * (50 - 60) = 57
		expect(getLiveUsage(C1, 'm1')?.rate).toBeCloseTo(57);
	});

	it('同じ messageId でも chatId が違えば別エントリ(混線しない)', () => {
		setLiveUsage(C1, 'm1', { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 });
		setLiveUsage(C2, 'm1', { prompt_tokens: 10, completion_tokens: 10, total_tokens: 20 });

		expect(getLiveUsage(C1, 'm1')?.total_tokens).toBe(2);
		expect(getLiveUsage(C2, 'm1')?.total_tokens).toBe(20);
	});

	it('chatId 無しの書き込みは捨てられる(宛先が作れない)', () => {
		setLiveUsage('', 'm1', { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 });
		expect(getActiveLiveUsage('')).toBeNull();
	});
});

describe('clearLiveUsage', () => {
	it('破棄でエントリが消える(揮発 — 確定値は message.usage / baseline 側)', () => {
		setLiveUsage(C1, 'm1', { prompt_tokens: 100, completion_tokens: 40, total_tokens: 140 });
		clearLiveUsage(C1, 'm1');
		expect(getLiveUsage(C1, 'm1')).toBeNull();
	});

	it('別 chat の同名 messageId は消さない', () => {
		setLiveUsage(C1, 'm1', { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 });
		setLiveUsage(C2, 'm1', { prompt_tokens: 10, completion_tokens: 10, total_tokens: 20 });
		clearLiveUsage(C1, 'm1');

		expect(getLiveUsage(C1, 'm1')).toBeNull();
		expect(getLiveUsage(C2, 'm1')?.total_tokens).toBe(20);
	});
});

describe('turnStatsFromUsage', () => {
	it('永続化済み usage(duration ターン累積)から所要秒と平均 tok/s を導く', () => {
		expect(
			turnStatsFromUsage({
				prompt_tokens: 9559,
				completion_tokens: 155,
				total_tokens: 9714,
				duration: 2.5,
				context_size: 262144
			})
		).toEqual({ seconds: 2.5, rate: 62 });
	});

	it('duration や completion の無い/ゼロの usage は null(非ストリーム経路等)', () => {
		expect(turnStatsFromUsage({ prompt_tokens: 10, completion_tokens: 5 })).toBeNull();
		expect(turnStatsFromUsage({ completion_tokens: 0, duration: 2.5 })).toBeNull();
		expect(turnStatsFromUsage({ completion_tokens: 5, duration: 0 })).toBeNull();
		expect(turnStatsFromUsage(null)).toBeNull();
	});
});

describe('contextUsageFromUsage', () => {
	it('永続化済み usage から占有(prompt 優先)と上限を導く', () => {
		expect(
			contextUsageFromUsage({
				prompt_tokens: 9559,
				completion_tokens: 155,
				total_tokens: 9714,
				context_size: 262144
			})
		).toEqual({ tokens: 9559, contextSize: 262144 });
	});

	it('prompt が無ければ total へフォールバック、context_size 無し/0 は contextSize: null', () => {
		expect(contextUsageFromUsage({ total_tokens: 9714 })).toEqual({
			tokens: 9714,
			contextSize: null
		});
		expect(contextUsageFromUsage({ prompt_tokens: 10, context_size: 0 })).toEqual({
			tokens: 10,
			contextSize: null
		});
	});

	it('数値の無い形・null は null', () => {
		expect(contextUsageFromUsage({ duration: 2.5 })).toBeNull();
		expect(contextUsageFromUsage(null)).toBeNull();
	});

	it('carried_tokens(次ターンへ持ち越す量)があれば prompt より優先する', () => {
		// backend が確定時に測り直した値 = 最後の呼び出しの prompt + 再送される出力
		expect(
			contextUsageFromUsage({
				prompt_tokens: 9559,
				completion_tokens: 155,
				total_tokens: 9714,
				carried_tokens: 9600,
				context_size: 262144
			})
		).toEqual({ tokens: 9600, contextSize: 262144 });
		// 数値でない carried は無視して従来の prompt へ
		expect(contextUsageFromUsage({ prompt_tokens: 10, carried_tokens: 'x' })).toEqual({
			tokens: 10,
			contextSize: null
		});
	});
});

describe('getActiveLiveUsage', () => {
	it('当該 chat の直近サンプルのエントリを返す', () => {
		setLiveUsage(C1, 'm1', { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 });
		vi.advanceTimersByTime(1000);
		setLiveUsage(C1, 'm2', { prompt_tokens: 10, completion_tokens: 10, total_tokens: 20 });

		expect(getActiveLiveUsage(C1)?.total_tokens).toBe(20);

		clearLiveUsage(C1, 'm2');
		expect(getActiveLiveUsage(C1)?.total_tokens).toBe(2);

		clearLiveUsage(C1, 'm1');
		expect(getActiveLiveUsage(C1)).toBeNull();
	});

	it('別 chat のエントリは混ざらない(subagent 子セッションの混線回帰)', () => {
		setLiveUsage(C1, 'm1', { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 });
		// 別 chat(子セッション等)がより新しいサンプルを積んでも、C1 の active は C1 の分
		vi.advanceTimersByTime(1000);
		setLiveUsage(C2, 'm9', { prompt_tokens: 100, completion_tokens: 100, total_tokens: 200 });

		expect(getActiveLiveUsage(C1)?.total_tokens).toBe(2);
		expect(getActiveLiveUsage(C2)?.total_tokens).toBe(200);
	});
});

describe('chat usage baseline', () => {
	it('Chat.meta.usage / chat:completion の usage を seed できる', () => {
		setChatUsageBaseline(C1, {
			prompt_tokens: 41267,
			completion_tokens: 1200,
			total_tokens: 42467,
			context_size: 262144
		});

		expect(getChatUsageBaseline(C1)).toEqual({
			prompt_tokens: 41267,
			completion_tokens: 1200,
			total_tokens: 42467,
			carried_tokens: undefined,
			context_size: 262144
		});
	});

	it('baseline は carried_tokens を保持し、UsageChip の占有は contextUsageFromUsage と一致する', () => {
		setChatUsageBaseline(C1, {
			prompt_tokens: 41267,
			completion_tokens: 1200,
			total_tokens: 42467,
			carried_tokens: 41500,
			context_size: 262144
		});
		const baseline = getChatUsageBaseline(C1);
		expect(baseline?.carried_tokens).toBe(41500);
		expect(contextUsageFromUsage(baseline)).toEqual({ tokens: 41500, contextSize: 262144 });
	});

	it('baseline は chat ごとに独立', () => {
		setChatUsageBaseline(C1, { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 });
		setChatUsageBaseline(C2, { prompt_tokens: 100, completion_tokens: 50, total_tokens: 150 });

		expect(getChatUsageBaseline(C1)?.prompt_tokens).toBe(10);
		expect(getChatUsageBaseline(C2)?.prompt_tokens).toBe(100);
	});

	it('null で消え、数値フィールドの無い形は据え置き', () => {
		setChatUsageBaseline(C1, { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 });
		setChatUsageBaseline(C1, { note: 'not-a-usage' });
		expect(getChatUsageBaseline(C1)?.prompt_tokens).toBe(10);

		setChatUsageBaseline(C1, null);
		expect(getChatUsageBaseline(C1)).toBeNull();
	});

	it("新規チャット('' スコープ)には baseline を置けない — 開始画面のゲージは常に空", () => {
		// 開始画面に前チャットの値が出るのは store ではなく $chatId の持ち越し側の不具合
		// (Chat.svelte onMount が '' へ戻す)。'' スコープ自体は書き込めないことを固定する。
		setChatUsageBaseline('', { prompt_tokens: 1200, completion_tokens: 30, total_tokens: 1230 });
		expect(getChatUsageBaseline('')).toBeNull();
	});

	it('resetChatUsage で当該 chat の baseline / live エントリが全部消える', () => {
		setChatUsageBaseline(C1, { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 });
		setLiveUsage(C1, 'm1', { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 });
		setLiveUsage(C1, 'm2', { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 });
		resetChatUsage(C1);

		expect(getChatUsageBaseline(C1)).toBeNull();
		expect(getActiveLiveUsage(C1)).toBeNull();
	});

	it('resetChatUsage は他 chat のスコープを消さない(走行中ターンの state 保護)', () => {
		setChatUsageBaseline(C2, { prompt_tokens: 100, completion_tokens: 50, total_tokens: 150 });
		setLiveUsage(C2, 'm9', { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 });
		setLiveUsage(C1, 'm1', { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 });

		resetChatUsage(C1);

		expect(getActiveLiveUsage(C1)).toBeNull();
		expect(getChatUsageBaseline(C2)?.prompt_tokens).toBe(100);
		expect(getLiveUsage(C2, 'm9')?.total_tokens).toBe(2);
	});
});

describe('UsageChip の表示値(chipUsage)', () => {
	// UsageChip は chipUsage(getActiveLiveUsage, getChatUsageBaseline, isLiveUsagePending)を描く
	const chip = (chatId: string) =>
		chipUsage(getActiveLiveUsage(chatId), getChatUsageBaseline(chatId), isLiveUsagePending(chatId));

	it('生成中の IN / OUT は前ターンの baseline ではなく live(数値と同じ出所)', () => {
		// 前ターンの確定値(IN 4.5k / OUT 5.2k)が baseline に残っている状態で 2 ターン目が走る
		setChatUsageBaseline(C1, {
			prompt_tokens: 4562,
			completion_tokens: 5300,
			total_tokens: 9862,
			carried_tokens: 5638,
			context_size: 262144
		});
		setLiveUsage(C1, 'm2', {
			prompt_tokens: 5700,
			completion_tokens: 40,
			total_tokens: 5740,
			context_size: 262144
		});

		expect(chip(C1)).toEqual({
			current: 5740,
			contextSize: 262144,
			inTokens: 5700,
			outTokens: 40,
			pending: false
		});

		// 次のサンプルで IN / OUT も追従する
		setLiveUsage(C1, 'm2', { prompt_tokens: 5700, completion_tokens: 90, total_tokens: 5790 });
		expect(chip(C1)?.outTokens).toBe(90);
		expect(chip(C1)?.current).toBe(5790);
	});

	it('確定後(live 破棄)は baseline の数値・IN / OUT に戻る(数値定義は従来どおり carried)', () => {
		setLiveUsage(C1, 'm2', { prompt_tokens: 5700, completion_tokens: 90, total_tokens: 5790 });
		setChatUsageBaseline(C1, {
			prompt_tokens: 5700,
			completion_tokens: 120,
			total_tokens: 5820,
			carried_tokens: 5760,
			context_size: 262144
		});
		clearLiveUsage(C1, 'm2');

		expect(chip(C1)).toEqual({
			current: 5760,
			contextSize: 262144,
			inTokens: 5700,
			outTokens: 120,
			pending: false
		});
	});

	it('新規チャットの 1 ターン目: 送信〜最初の chat:usage の間も消えない(pending)', () => {
		expect(chip(C1)).toBeNull(); // 開始画面は何も描かない

		markLiveUsagePending(C1, 'm1'); // 最初の snapshot(content_blocks: [])
		expect(chip(C1)).toEqual({
			current: null,
			contextSize: null,
			inTokens: undefined,
			outTokens: undefined,
			pending: true
		});

		// 最初のサンプルで live に切り替わり、pending は外れる
		setLiveUsage(C1, 'm1', {
			prompt_tokens: 4562,
			completion_tokens: 0,
			total_tokens: 4562,
			context_size: 262144
		});
		expect(isLiveUsagePending(C1)).toBe(false);
		expect(chip(C1)?.current).toBe(4562);

		// 後続の snapshot で印が付き直さない(live が既にある)
		markLiveUsagePending(C1, 'm1');
		expect(isLiveUsagePending(C1)).toBe(false);

		// done → baseline。送信から完了まで chip が null になる瞬間が無い
		setChatUsageBaseline(C1, {
			prompt_tokens: 4562,
			completion_tokens: 60,
			total_tokens: 4622,
			carried_tokens: 4580,
			context_size: 262144
		});
		clearLiveUsage(C1, 'm1');
		expect(chip(C1)?.current).toBe(4580);
	});

	it('既知値(baseline)があれば pending 中もそれを出し続ける', () => {
		setChatUsageBaseline(C1, {
			prompt_tokens: 4562,
			completion_tokens: 60,
			total_tokens: 4622,
			carried_tokens: 4580,
			context_size: 262144
		});
		markLiveUsagePending(C1, 'm2');
		expect(chip(C1)).toMatchObject({
			current: 4580,
			inTokens: 4562,
			outTokens: 60,
			pending: false
		});
	});

	it('pending は clearLiveUsage / resetChatUsage で外れ、chat ごとに独立', () => {
		markLiveUsagePending(C1, 'm1');
		markLiveUsagePending(C2, 'm9');
		clearLiveUsage(C1, 'm1');
		expect(isLiveUsagePending(C1)).toBe(false);
		expect(chip(C1)).toBeNull();
		expect(isLiveUsagePending(C2)).toBe(true);

		resetChatUsage(C2);
		expect(isLiveUsagePending(C2)).toBe(false);
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
