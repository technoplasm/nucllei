import { describe, expect, it } from 'vitest';
import { formatDurationSeconds } from './duration';

describe('formatDurationSeconds', () => {
	it('1 秒未満を潰さない(#100 の「0秒間の思考」の再発防止)', () => {
		expect(formatDurationSeconds(0.4)).toBe('0.4');
		expect(formatDurationSeconds(0.04)).toBe('0.0');
		expect(formatDurationSeconds(0)).toBe('0.0');
	});

	it('10 秒未満は 0.1 秒刻み、以降は整数', () => {
		expect(formatDurationSeconds(1.24)).toBe('1.2');
		expect(formatDurationSeconds(9.99)).toBe('10.0');
		expect(formatDurationSeconds(12.347000122)).toBe('12');
		expect(formatDurationSeconds(125.6)).toBe('126');
	});

	it('非数・負値は 0 に落とす', () => {
		expect(formatDurationSeconds(-1)).toBe('0');
		expect(formatDurationSeconds(NaN)).toBe('0');
		expect(formatDurationSeconds(Infinity)).toBe('0');
		expect(formatDurationSeconds(undefined)).toBe('0');
		expect(formatDurationSeconds('3')).toBe('0');
	});
});
