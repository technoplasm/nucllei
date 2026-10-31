import { describe, expect, it } from 'vitest';

import { pickLocalVoice } from './speech-synthesis';

const v = (voiceURI: string, lang: string, localService: boolean, isDefault = false) => ({
	voiceURI,
	lang,
	localService,
	default: isDefault
});

const voices = [
	v('Google 日本語', 'ja-JP', false),
	v('Microsoft Nanami Online (Natural) - Japanese (Japan)', 'ja-JP', false),
	v('Microsoft Haruka - Japanese (Japan)', 'ja-JP', true),
	v('Microsoft Ayumi - Japanese (Japan)', 'ja-JP', true),
	v('Microsoft Zira - English (United States)', 'en-US', true, true),
	v('Google US English', 'en-US', false)
];

describe('pickLocalVoice', () => {
	it('表示言語の端末内の声だけを選ぶ (クラウド合成の声は使わない)', () => {
		expect(pickLocalVoice(voices, 'ja-JP')?.voiceURI).toBe('Microsoft Haruka - Japanese (Japan)');
	});

	it('設定の声が端末内で言語も合えばそれを使う', () => {
		expect(pickLocalVoice(voices, 'ja-JP', 'Microsoft Ayumi - Japanese (Japan)')?.voiceURI).toBe(
			'Microsoft Ayumi - Japanese (Japan)'
		);
	});

	it('設定の声がクラウド合成なら使わず、端末内の声に替える', () => {
		expect(pickLocalVoice(voices, 'ja-JP', 'Google 日本語')?.voiceURI).toBe(
			'Microsoft Haruka - Japanese (Japan)'
		);
	});

	it('設定の声が別言語なら使わない', () => {
		expect(
			pickLocalVoice(voices, 'ja-JP', 'Microsoft Zira - English (United States)')?.voiceURI
		).toBe('Microsoft Haruka - Japanese (Japan)');
	});

	it('表示言語の端末内の声が無ければ null (クラウドの声しか無い場合も)', () => {
		expect(pickLocalVoice(voices, 'zh-CN')).toBeNull();
		expect(pickLocalVoice([v('Google 日本語', 'ja-JP', false)], 'ja-JP')).toBeNull();
		expect(pickLocalVoice([], 'ja-JP')).toBeNull();
	});

	it('言語タグの表記揺れ (ja_JP / 大文字小文字 / 地域なし) を吸収し、地域まで合う声を優先する', () => {
		const list = [v('ja-generic', 'ja', true), v('android-ja', 'ja_JP', true)];
		expect(pickLocalVoice(list, 'ja-JP')?.voiceURI).toBe('android-ja');
		expect(pickLocalVoice([v('ja-generic', 'JA', true)], 'ja-JP')?.voiceURI).toBe('ja-generic');
	});

	it('候補の中では既定の声を優先する', () => {
		const list = [v('a', 'en-US', true), v('b', 'en-US', true, true)];
		expect(pickLocalVoice(list, 'en-US')?.voiceURI).toBe('b');
	});
});
