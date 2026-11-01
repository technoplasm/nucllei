import { describe, expect, it } from 'vitest';
import i18next from 'i18next';
import { i18nOptions } from './index';
import jaJP from './locales/ja-JP/translation.json';
import enUS from './locales/en-US/translation.json';

const createT = async (lng: string) => {
	const instance = i18next.createInstance();
	await instance.init({
		...i18nOptions,
		lng,
		fallbackLng: 'en-US',
		resources: {
			'ja-JP': { translation: jaJP },
			'en-US': { translation: enUS }
		}
	});
	return instance.t;
};

describe('i18n のキー', () => {
	it('`:` を含むキーを名前空間の区切りとして読まない', async () => {
		const t = await createT('ja-JP');
		expect(t('Contact:')).toBe('お問い合わせ:');
		expect(t('Warning:')).toBe('警告:');
		expect(t('Warning: {{reason}}', { reason: 'x' })).toBe('警告: x');
	});

	it('`.` を含むキーも引ける', async () => {
		const t = await createT('ja-JP');
		expect(t('Enter URL (e.g. http://localhost:11434)')).toBe(
			'URL を入力してください (例: http://localhost:11434)'
		);
		expect(t('Enter your code here...')).toBe('ここにコードを入力...');
	});

	it('en-US は訳が空なのでキーの原文をそのまま返す', async () => {
		const t = await createT('en-US');
		expect(t('Contact:')).toBe('Contact:');
		expect(t('Warning:')).toBe('Warning:');
		expect(t('Enter your code here...')).toBe('Enter your code here...');
	});

	it('ja-JP の全キーが訳 (空なら原文) を返す', async () => {
		const t = await createT('ja-JP');
		for (const [key, value] of Object.entries(jaJP as Record<string, string>)) {
			if (key.includes('{{')) continue;
			expect(t(key), key).toBe(value === '' ? key : value);
		}
	});
});
