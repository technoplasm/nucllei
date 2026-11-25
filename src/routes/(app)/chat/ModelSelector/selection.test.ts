// チャットの選択欄に置くモデル id の規則(selection.ts)。
// 利用者が読めない preset のチャットを開いたとき、選択欄が空にならず、送信は止まる

import { describe, it, expect } from 'vitest';

import { isUnavailableModel, normalizeSelectedModels } from './selection';

const AVAILABLE = ['glm-chat', 'kimi'];

describe('normalizeSelectedModels', () => {
	it('一覧にあるモデルはそのまま残す', () => {
		expect(normalizeSelectedModels(['kimi'], AVAILABLE, null)).toEqual(['kimi']);
	});

	it('一覧に無いモデルは空に落とす(記録されたモデルでなければ)', () => {
		expect(normalizeSelectedModels(['hidden-sub'], AVAILABLE, null)).toEqual(['']);
		expect(normalizeSelectedModels(['hidden-sub'], AVAILABLE, 'other')).toEqual(['']);
	});

	it('開いているチャットに記録されたモデルは、一覧に無くても残す', () => {
		expect(normalizeSelectedModels(['hidden-sub'], AVAILABLE, 'hidden-sub')).toEqual([
			'hidden-sub'
		]);
	});

	it('別のモデルを選んだら、記録されたモデルがあってもそちらに従う', () => {
		expect(normalizeSelectedModels(['kimi'], AVAILABLE, 'hidden-sub')).toEqual(['kimi']);
	});

	it('1 モデルに切り詰める', () => {
		expect(normalizeSelectedModels(['kimi', 'glm-chat'], AVAILABLE, null)).toEqual(['kimi']);
		expect(normalizeSelectedModels(['hidden-sub', 'kimi'], AVAILABLE, 'hidden-sub')).toEqual([
			'hidden-sub'
		]);
	});

	it('未選択は未選択のまま(記録が空でも残さない)', () => {
		expect(normalizeSelectedModels([''], AVAILABLE, '')).toEqual(['']);
		expect(normalizeSelectedModels([], AVAILABLE, 'hidden-sub')).toEqual([]);
	});
});

describe('isUnavailableModel', () => {
	it('一覧に無いモデルは送れない', () => {
		expect(isUnavailableModel('hidden-sub', AVAILABLE)).toBe(true);
	});

	it('一覧にあるモデルと未選択は「送れない」ではない', () => {
		expect(isUnavailableModel('kimi', AVAILABLE)).toBe(false);
		expect(isUnavailableModel('', AVAILABLE)).toBe(false);
		expect(isUnavailableModel(undefined, AVAILABLE)).toBe(false);
	});
});
