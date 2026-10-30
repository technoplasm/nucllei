import { describe, expect, it } from 'vitest';

import { estimateTokens, splitToolDescription, toolVaries } from './prompt-preview';

const tool = (description: string, varies: any = null) => ({
	name: 't',
	description,
	parameters: {},
	chars: 0,
	varies
});

describe('estimateTokens', () => {
	it('ASCII は 4 文字で 1、それ以外は 1 文字で 1 (切り上げ)', () => {
		expect(estimateTokens('')).toBe(0);
		expect(estimateTokens('abcd')).toBe(1);
		expect(estimateTokens('abcde')).toBe(2);
		expect(estimateTokens('日本語')).toBe(3);
		expect(estimateTokens('ab日本')).toBe(3);
	});
});

describe('splitToolDescription', () => {
	it('description_from で固定と利用者ごとに分ける', () => {
		expect(splitToolDescription(tool('固定一覧', { description_from: 2 }))).toEqual({
			fixed: '固定',
			varies: '一覧'
		});
	});
	it('varies が無ければ全体が固定、overridden なら全体が利用者ごと', () => {
		expect(splitToolDescription(tool('説明'))).toEqual({ fixed: '説明', varies: '' });
		expect(splitToolDescription(tool('説明', { overridden: true }))).toEqual({
			fixed: '',
			varies: '説明'
		});
	});
	it('toolVaries', () => {
		expect(toolVaries(tool('x'))).toBe(false);
		expect(toolVaries(tool('x', { params: ['skill_id'] }))).toBe(true);
		expect(toolVaries(tool('x', { description_from: 0 }))).toBe(true);
	});
});
