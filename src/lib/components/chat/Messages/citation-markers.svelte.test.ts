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

describe('リンクによる簡易引用(#146 の簡易版)', () => {
	it('[/ext/…] と添付の [file_id] は新しいタブのリンク、[1] は文字のまま', () => {
		const fid = '0b6c8c0e-1d2f-4a3b-9c8d-7e6f5a4b3c2d';
		const { body } = render(Markdown, {
			props: {
				id: 'm',
				content: `根拠 [/ext/cage/doc/a#節] と [${fid}] と [1]。`,
				fileNames: new Map([[fid, '手順書.pdf']])
			},
			context: i18nContext()
		});
		expect(body).toContain('href="/ext/cage/doc/a#%E7%AF%80"');
		expect(body).toContain(`href="/api/v1/files/${fid}/content"`);
		expect(body).toContain('手順書.pdf');
		expect(body).toContain('a › 節');
		expect(body.match(/target="_blank"/g)?.length).toBe(2);
		expect(body).toContain('[1]。');
	});
});

describe('番号チップ(#146 簡易版、2026-10-06 吹き出し化)', () => {
	it('チップはリンクではなく吹き出しのトリガー — 押してもページは開かない、コピーは「[1]」', async () => {
		const { CitationScope } = await import('./citations.svelte');
		const { collectCiteRefs } = await import('$lib/utils/citations');
		const { lex } = await import('$lib/utils/markdown-it');
		const content = '根拠 [/ext/cage/doc/a#節] と [/ext/cage/doc/a#:~:text=引用]。';
		// SSR では $effect が走らないので、Markdown が書き込むはずの引用列を先に入れておく
		const citations = new CitationScope();
		citations.set(0, collectCiteRefs(lex(content)));
		const { body } = render(Markdown, {
			props: { id: 'm', content, citations, citationSlot: 0 },
			context: i18nContext()
		});
		// 同じページの 2 箇所は同じ番号 1 のチップ 2 つ。どちらも role=button で href を持たない
		const chips = body.match(/<span[^>]*role="button"[^>]*>/g) ?? [];
		expect(chips).toHaveLength(2);
		for (const chip of chips) {
			expect(chip).toContain('aria-haspopup="dialog"');
			expect(chip).toContain('aria-expanded="false"');
			expect(chip).toContain('tabindex="0"');
			expect(chip).not.toContain('href=');
		}
		expect(body).not.toContain('href="/ext/cage/doc/a');
		expect(body).toContain('<span class="sr-only">[</span>1<span class="sr-only">]</span>');
	});
});

describe('開けない出典(sandbox のパス・このチャットに無い file_id)', () => {
	const fid = '0b6c8c0e-1d2f-4a3b-9c8d-7e6f5a4b3c2d';
	const other = '11111111-2222-3333-4444-555555555555';
	const fileNames = new Map([[fid, '手順書.pdf']]);
	const content = `根拠 [/ext/cage/doc/a#節] と [/workspace/notes/b.md] と [${fid}] と [${other}] と [/workspace/notes/b.md#:~:text=結論]。`;

	const scoped = async () => {
		const { CitationScope } = await import('./citations.svelte');
		const { collectCiteRefs } = await import('$lib/utils/citations');
		const { lex } = await import('$lib/utils/markdown-it');
		const citations = new CitationScope();
		citations.set(0, collectCiteRefs(lex(content, { fileNames })));
		return citations;
	};

	it('チップ: 開ける出典は吹き出しのトリガー、開けない出典は押せない(破線・打ち消し線、理由は title と aria-label)', async () => {
		const citations = await scoped();
		const { body } = render(Markdown, {
			props: { id: 'm', content, fileNames, citations, citationSlot: 0 },
			context: i18nContext()
		});
		const buttons = body.match(/<span[^>]*role="button"[^>]*>/g) ?? [];
		const invalid = body.match(/<span[^>]*role="img"[^>]*>/g) ?? [];
		// 番号: /ext/a = 1, /workspace/notes/b.md = 2(2 箇所), 添付 = 3, 無い添付 = 4
		expect(buttons).toHaveLength(2);
		expect(invalid).toHaveLength(3);
		for (const chip of invalid) {
			expect(chip).toContain('line-through');
			expect(chip).toContain('border-dashed');
			expect(chip).not.toContain('tabindex');
			expect(chip).not.toContain('aria-haspopup');
			expect(chip).not.toContain('href=');
		}
		expect(invalid[0]).toContain(
			'aria-label="[2] Unopenable source: /workspace/notes/b.md — It points to a file in the sandbox, which cannot be opened from the chat."'
		);
		expect(invalid[0]).toContain(
			'title="Unopenable source: /workspace/notes/b.md — It points to a file in the sandbox'
		);
		expect(invalid[1]).toContain(
			`aria-label="[4] Unopenable source: ${other} — It points to a file that is not attached to this chat`
		);
		expect(invalid[2]).toContain(
			'aria-label="[2] Unopenable source: /workspace/notes/b.md › 「結論」'
		);
		// 元の [/workspace/…] の文字は本文に残さない(番号チップに置き換わる)
		expect(body).not.toContain('[/workspace/');
	});

	it('出典一覧: 同じ番号で、開ける出典はリンク、開けない出典はリンクにせず「開けない出典」の札', async () => {
		const citations = await scoped();
		const { default: CitationList } = await import('./CitationList.svelte');
		const { body } = render(CitationList, { props: { citations }, context: i18nContext() });
		const items = body.match(/<li[\s\S]*?<\/li>/g) ?? [];
		expect(items).toHaveLength(4);
		expect(items.map((li) => li.match(/>(\d+)\.</)?.[1])).toEqual(['1', '2', '3', '4']);
		expect(items[0]).toContain('href="/ext/cage/doc/a"');
		expect(items[2]).toContain(`href="/api/v1/files/${fid}/content"`);
		for (const i of [1, 3]) {
			expect(items[i]).not.toContain('<a');
			expect(items[i]).toContain('Unopenable source');
			expect(items[i]).toContain('line-through');
		}
		expect(items[1]).toContain('/workspace/notes/b.md');
		expect(items[1]).toContain('› 「結論」');
		expect(items[1]).toContain(
			'title="It points to a file in the sandbox, which cannot be opened from the chat."'
		);
		expect(items[3]).toContain(other);
		expect(items[3]).toContain('It points to a file that is not attached to this chat');
		for (const i of [0, 2]) expect(items[i]).not.toContain('Unopenable source');
	});

	it('番号を振らない場所(tool の結果・ユーザー発言等)では元の [/workspace/…] の文字のまま', () => {
		const { body } = render(Markdown, {
			props: { id: 'm', content: '保存先 [/workspace/notes/b.md]。', fileNames },
			context: i18nContext()
		});
		expect(body.replace(/<!--[^>]*-->/g, '')).toContain('保存先 [/workspace/notes/b.md]。');
		expect(body).not.toContain('role="img"');
	});
});
