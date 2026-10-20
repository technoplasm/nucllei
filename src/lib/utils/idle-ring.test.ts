import { describe, expect, it } from 'vitest';
import {
	formatIdleRemaining,
	idleRingFraction,
	isIdleRingPinned,
	isIdleRingVisible,
	idleRingLevel,
	idleRingWidestText,
	msUntilNextIdleTick
} from './idle-ring';

const MIN = 60 * 1000;
const TIMEOUT = 30 * MIN;

describe('isIdleRingVisible(普段は隠す・ホバーで出す・残り 1/2 未満は常に出す)', () => {
	it('残りが 1/2 以上でホバーしていなければ隠す', () => {
		expect(isIdleRingVisible({ remainingMs: TIMEOUT, timeoutMs: TIMEOUT, hovered: false })).toBe(
			false
		);
		expect(
			isIdleRingVisible({ remainingMs: TIMEOUT / 2, timeoutMs: TIMEOUT, hovered: false })
		).toBe(false);
	});

	it('ホバーしていれば残りに関係なく出す', () => {
		expect(isIdleRingVisible({ remainingMs: TIMEOUT, timeoutMs: TIMEOUT, hovered: true })).toBe(
			true
		);
	});

	it('残りが 1/2 を切ったらホバーしなくても常に出す(ホバーの無いモバイルも同じ)', () => {
		expect(
			isIdleRingVisible({ remainingMs: TIMEOUT / 2 - 1, timeoutMs: TIMEOUT, hovered: false })
		).toBe(true);
		expect(isIdleRingVisible({ remainingMs: 0, timeoutMs: TIMEOUT, hovered: false })).toBe(true);
		expect(isIdleRingPinned(TIMEOUT / 2 - 1, TIMEOUT)).toBe(true);
		expect(isIdleRingPinned(TIMEOUT / 2, TIMEOUT)).toBe(false);
		// 以前のしきい値 1/3 より手前から出る
		expect(isIdleRingPinned(TIMEOUT * 0.4, TIMEOUT)).toBe(true);
	});
});

describe('idleRingFraction', () => {
	it('満タン = 1、失効 = 0 で、残りに比例して減る', () => {
		expect(idleRingFraction(TIMEOUT, TIMEOUT)).toBe(1);
		expect(idleRingFraction(TIMEOUT / 2, TIMEOUT)).toBe(0.5);
		expect(idleRingFraction(0, TIMEOUT)).toBe(0);
	});

	it('範囲外は丸める(全長 0 は 0)', () => {
		expect(idleRingFraction(TIMEOUT * 2, TIMEOUT)).toBe(1);
		expect(idleRingFraction(-1, TIMEOUT)).toBe(0);
		expect(idleRingFraction(10, 0)).toBe(0);
	});
});

describe('idleRingLevel(色の段階は割合だけ: 1/2 で出ても色はそのまま・1/4 で黄・1/8 で赤)', () => {
	const TTL20 = 20 * MIN;
	const SEC = 1000;

	it('TTL 20m: 10:00 で常に出る(色は普段のまま)→ 5:00 で黄 → 2:30 で赤', () => {
		expect(isIdleRingPinned(10 * MIN, TTL20)).toBe(false);
		expect(isIdleRingPinned(10 * MIN - 1, TTL20)).toBe(true);
		expect(formatIdleRemaining(10 * MIN - 1)).toBe('10:00');
		expect(idleRingLevel(10 * MIN - 1, TTL20)).toBe('normal');

		expect(idleRingLevel(5 * MIN, TTL20)).toBe('normal');
		expect(idleRingLevel(5 * MIN - 1, TTL20)).toBe('caution');
		expect(formatIdleRemaining(5 * MIN - 1)).toBe('5:00');

		expect(idleRingLevel(2 * MIN + 30 * SEC, TTL20)).toBe('caution');
		expect(idleRingLevel(2 * MIN + 30 * SEC - 1, TTL20)).toBe('danger');
		expect(formatIdleRemaining(2 * MIN + 30 * SEC - 1)).toBe('2:30');
		expect(idleRingLevel(0, TTL20)).toBe('danger');
	});

	it('「最後の 1 分」の規則は無い — 長い TTL でも割合で決まる', () => {
		const ttl = 4 * 60 * MIN;
		expect(idleRingLevel(59 * MIN, ttl)).toBe('caution');
		expect(idleRingLevel(29 * MIN, ttl)).toBe('danger');
		expect(idleRingLevel(MIN + 1, TTL20)).toBe('danger');
	});

	it('満タンは普段の色', () => {
		expect(idleRingLevel(TIMEOUT, TIMEOUT)).toBe('normal');
	});
});

describe('formatIdleRemaining', () => {
	it('1 時間未満は m:ss', () => {
		expect(formatIdleRemaining(12 * MIN + 34 * 1000)).toBe('12:34');
		expect(formatIdleRemaining(5 * 1000)).toBe('0:05');
	});

	it('1 時間以上は h:mm:ss', () => {
		expect(formatIdleRemaining(4 * 60 * MIN)).toBe('4:00:00');
		expect(formatIdleRemaining(60 * MIN + 5 * 1000)).toBe('1:00:05');
		expect(formatIdleRemaining(24 * 60 * MIN)).toBe('24:00:00');
	});

	it('秒は切り上げ — 0:00 になるのは残りがちょうど 0 のときだけ', () => {
		expect(formatIdleRemaining(1)).toBe('0:01');
		expect(formatIdleRemaining(999)).toBe('0:01');
		expect(formatIdleRemaining(0)).toBe('0:00');
		expect(formatIdleRemaining(-5)).toBe('0:00');
	});
});

describe('msUntilNextIdleTick', () => {
	it('表示の秒が切り替わる瞬間に合わせる', () => {
		expect(msUntilNextIdleTick(12_345)).toBe(345);
		expect(msUntilNextIdleTick(12_000)).toBe(1000);
		expect(msUntilNextIdleTick(1)).toBe(1);
	});
});

describe('idleRingWidestText(枠が常に確保する文字の幅)', () => {
	it('全長の表示の数字を 0 にした形', () => {
		expect(idleRingWidestText(10 * MIN)).toBe('00:00');
		expect(idleRingWidestText(5 * MIN)).toBe('0:00');
		expect(idleRingWidestText(2 * 60 * MIN)).toBe('0:00:00');
		expect(idleRingWidestText(24 * 60 * MIN)).toBe('00:00:00');
	});

	it('残りが全長以下なら、表示はこれより長くならない(出し入れ・カウントダウンで枠が動かない)', () => {
		for (const timeout of [90 * 1000, 10 * MIN, TIMEOUT, 60 * MIN, 4 * 60 * MIN]) {
			const widest = idleRingWidestText(timeout).length;
			for (const remaining of [timeout, timeout - 1, timeout / 3, MIN, 1, 0]) {
				expect(formatIdleRemaining(remaining).length).toBeLessThanOrEqual(widest);
			}
		}
	});
});
