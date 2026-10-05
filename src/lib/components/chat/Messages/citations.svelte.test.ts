import { describe, it, expect } from 'vitest';

import { lex } from '$lib/utils/markdown-it';
import { collectCiteRefs } from '$lib/utils/citations';
import { CitationScope } from './citations.svelte';

const refs = (src: string) => collectCiteRefs(lex(src));

describe('CitationScope(メッセージ単位の通し番号、#146 簡易版)', () => {
	it('ブロック番号順に通しで振る(書き込みの順ではなく)', () => {
		const scope = new CitationScope();
		// tool 呼び出し(block 1)を挟んだ後の text(block 2)が先に書き込まれても番号はブロック順
		scope.set(2, refs('[/ext/b] [/ext/a#節]'));
		scope.set(0, refs('[/ext/a]'));
		expect(scope.numberOf('/ext/a')).toBe(1);
		expect(scope.numberOf('/ext/b')).toBe(2);
		expect(scope.current.entries[0].sections.map((s) => s.label)).toEqual(['節']);
	});

	it('ブロックが消えたら(編集・再描画)その引用も一覧から外れる', () => {
		const scope = new CitationScope();
		scope.set(0, refs('[/ext/a]'));
		scope.set(2, refs('[/ext/b]'));
		scope.clear(0);
		expect(scope.numberOf('/ext/a')).toBeUndefined();
		expect(scope.numberOf('/ext/b')).toBe(1);
		scope.set(2, []);
		expect(scope.current.entries).toEqual([]);
	});
});
