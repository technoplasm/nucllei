// @vitest-environment node
// 本文中の [n] を <source_id> の引用チップに置換する機構は撤去した(2026-10-05、refs #142)。
// markdown-it 移行後は source_id が HTML として通らず生文字列で漏れていた — [n] は素の文字列、
// [n](url) は普通のリンクとして描画されることを固定する。
import { describe, it, expect } from 'vitest';
import { render } from 'svelte/server';

import Markdown from './Markdown.svelte';

const i18nContext = () =>
	new Map([
		['i18n', { subscribe: (fn: (v: unknown) => void) => (fn({ t: (s: string) => s }), () => {}) }]
	]);

describe('引用マーカー [n]', () => {
	it('本文の [n] は素の文字列、[n](url) はリンクとして描画する', () => {
		const { body } = render(Markdown, {
			props: { id: 'm', content: '根拠は第3条 [1]。原本 [2](https://example.com/a.pdf)。' },
			context: i18nContext()
		});
		expect(body).not.toContain('source_id');
		expect(body).toContain('根拠は第3条 [1]。');
		expect(body).toContain('href="https://example.com/a.pdf"');
	});
});
