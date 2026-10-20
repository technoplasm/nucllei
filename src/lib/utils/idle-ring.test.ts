import { describe, expect, it } from 'vitest';
import {
	formatIdleRemaining,
	idleRingFraction,
	isIdleRingPinned,
	isIdleRingVisible,
	isIdleRingWarning,
	msUntilNextIdleTick
} from './idle-ring';

const MIN = 60 * 1000;
const TIMEOUT = 30 * MIN;

describe('isIdleRingVisible(普段は隠す・ホバーで出す・残り 1/3 未満は常に出す)', () => {
	it('残りが 1/3 以上でホバーしていなければ隠す', () => {
		expect(isIdleRingVisible({ remainingMs: TIMEOUT, timeoutMs: TIMEOUT, hovered: false })).toBe(
			false
		);
		expect(
			isIdleRingVisible({ remainingMs: TIMEOUT / 3, timeoutMs: TIMEOUT, hovered: false })
		).toBe(false);
	});

	it('ホバーしていれば残りに関係なく出す', () => {
		expect(isIdleRingVisible({ remainingMs: TIMEOUT, timeoutMs: TIMEOUT, hovered: true })).toBe(
			true
		);
	});

	it('残りが 1/3 を切ったらホバーしなくても常に出す(ホバーの無いモバイルも同じ)', () => {
		expect(
			isIdleRingVisible({ remainingMs: TIMEOUT / 3 - 1, timeoutMs: TIMEOUT, hovered: false })
		).toBe(true);
		expect(isIdleRingVisible({ remainingMs: 0, timeoutMs: TIMEOUT, hovered: false })).toBe(true);
		expect(isIdleRingPinned(TIMEOUT / 3 - 1, TIMEOUT)).toBe(true);
		expect(isIdleRingPinned(TIMEOUT / 3, TIMEOUT)).toBe(false);
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

describe('isIdleRingWarning', () => {
	it('最後の 1 分だけ警告色', () => {
		expect(isIdleRingWarning(MIN + 1, TIMEOUT)).toBe(false);
		expect(isIdleRingWarning(MIN, TIMEOUT)).toBe(true);
		expect(isIdleRingWarning(0, TIMEOUT)).toBe(true);
	});

	it('全長が短ければ 1/3 から(常に見えている間だけ警告する)', () => {
		const short = 90 * 1000;
		expect(isIdleRingWarning(31 * 1000, short)).toBe(false);
		expect(isIdleRingWarning(30 * 1000, short)).toBe(true);
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
