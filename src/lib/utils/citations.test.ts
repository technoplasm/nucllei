import { describe, it, expect } from 'vitest';
import { lex } from './markdown-it';
import { collectCiteRefs, numberCitations, resolveBracketRef, type CiteEnv } from './citations';

// 番号付き引用(#146 の簡易版)。メッセージ = テキストブロックの列
const cite = (blocks: string[], env: CiteEnv = {}) =>
	numberCitations(blocks.map((src) => collectCiteRefs(lex(src, env))));

/** 本文に出てきた順の番号列(チップに出る数字) */
const chipNumbers = (blocks: string[], env: CiteEnv = {}) => {
	const { numbers } = cite(blocks, env);
	return blocks.flatMap((src) => collectCiteRefs(lex(src, env)).map((r) => numbers.get(r.key)));
};

const FID = '0b6c8c0e-1d2f-4a3b-9c8d-7e6f5a4b3c2d';
const env: CiteEnv = { fileNames: new Map([[FID, '手順書.pdf']]) };

const A = '/ext/cage/doc/院内マニュアル/感染対策マニュアル/02_14-2_針刺し';
const B = '/ext/cage/static/kansen_manual/docs/14-2.pdf';

describe('Text Fragment の組み直し(staging 2026-10-06: モデルが `text=` を落とす)', () => {
	const enc = (s: string) => encodeURIComponent(s);
	const page = A.split('/').map(enc).join('/');

	it('`#:~:引用文`(text= 抜け)は `#:~:text=` に直し、表示は「引用文」', () => {
		const r = resolveBracketRef(`${A}#:~:曝露後できるだけ早く`)!;
		expect(r.href).toBe(`${A}#:~:text=${enc('曝露後できるだけ早く')}`);
		expect(r.section).toBe('「曝露後できるだけ早く」');
	});

	it('正しい `#:~:text=` も同じ形(二重にしない)', () => {
		const r = resolveBracketRef(`${A}#:~:text=曝露後できるだけ早く`)!;
		expect(r.href).toBe(`${A}#:~:text=${enc('曝露後できるだけ早く')}`);
	});

	it('空白・%・- , & は Text Fragment の規則でエンコード(指令の区切りにしない)', () => {
		const r = resolveBracketRef(`${A}#:~:おおまかに HBV 30%`)!;
		expect(r.href.endsWith(`#:~:text=${enc('おおまかに HBV 30%')}`)).toBe(true);
		const r2 = resolveBracketRef(`${A}#:~:text=1-2 時間, A&B`)!;
		expect(r2.href.endsWith('#:~:text=1%2D2%20%E6%99%82%E9%96%93%2C%20A%26B')).toBe(true);
		expect(r2.section).toBe('「1-2 時間, A&B」');
	});

	it('markdown-it 経由(normalizeLink)でもページ部は percent-encode、引用文は組み直し', () => {
		const [ref] = collectCiteRefs(lex(`本文 [${A}#:~:1 回目の予防内服を]`));
		expect(ref.href).toBe(`${page}#:~:text=${enc('1 回目の予防内服を')}`);
	});

	it('見出しの先頭のローマ数字は外して cage の別名へ(写し間違えても飛べる)', () => {
		for (const f of [
			'ⅩⅠ．予防措置と効果',
			'Ⅹ．予防措置と効果',
			'Ⅺ．予防措置と効果',
			'Ⅱ-1 予防措置と効果'
		]) {
			const r = resolveBracketRef(`${A}#${f}`)!;
			expect(r.href).toBe(`${A}#予防措置と効果`);
			expect(r.section).toBe('予防措置と効果');
		}
	});

	it('アラビア数字の番号・番号の無い見出しはそのまま', () => {
		expect(resolveBracketRef(`${A}#1. 概要`)!.href).toBe(`${A}#1. 概要`);
		expect(resolveBracketRef(`${A}#Ⅱ型糖尿病`)!.href).toBe(`${A}#Ⅱ型糖尿病`);
		expect(resolveBracketRef(`${A}#実習生の対応`)!.href).toBe(`${A}#実習生の対応`);
	});
});

describe('番号付き引用(#146 の簡易版)', () => {
	it('[URL1] [URL1] [URL2] → 1 1 2', () => {
		expect(chipNumbers([`a [${A}] b [${A}] c [${B}]`])).toEqual([1, 1, 2]);
	});

	it('同じページの別の節(#見出し / #:~:text=)は同じ番号、一覧には節を初出順に重複なく並べる', () => {
		const src = `x [${A}#Ⅱ．洗浄法] y [${B}] z [${A}] w [${A}#:~:text=流水] v [${A}#Ⅱ．洗浄法]`;
		expect(chipNumbers([src])).toEqual([1, 2, 1, 1, 1]);
		const { entries } = cite([src]);
		expect(entries.map((e) => [e.n, e.title])).toEqual([
			[1, '02_14-2_針刺し'],
			[2, '14-2.pdf']
		]);
		// ローマ数字の番号は外して見せ、リンクも番号を除いた別名へ(モデルが写し間違えるので)
		expect(entries[0].sections.map((s) => s.label)).toEqual(['洗浄法', '「流水」']);
		// 一覧の行はページ、節の行とチップはその箇所(fragment 付き)へ
		expect(decodeURI(entries[0].pageHref)).toBe(A);
		expect(decodeURI(entries[0].sections[0].href)).toBe(`${A}#洗浄法`);
		expect(entries[1].sections).toEqual([]);
	});

	it('percent-encode の有無が違っても同じページは同じ番号', () => {
		expect(chipNumbers([`[/ext/a/図.png] [/ext/a/%E5%9B%B3.png#x]`])).toEqual([1, 1]);
	});

	it('file_id も同じ番号付けに入る(表示はファイル名)', () => {
		const { entries } = cite([`[${A}] [${FID}] [${A}#節] [${FID}]`], env);
		expect(chipNumbers([`[${A}] [${FID}] [${A}#節] [${FID}]`], env)).toEqual([1, 2, 1, 2]);
		expect(entries[1]).toMatchObject({ n: 2, kind: 'file', title: '手順書.pdf' });
		expect(entries[1].pageHref).toBe(`/api/v1/files/${FID}/content`);
	});

	it('テキストブロックをまたいで通し番号(tool 呼び出しを挟んだ前後の text も)', () => {
		const blocks = [`まず [${B}]。`, `次に [${A}] と [${B}#p3]。`, `最後に [${FID}] と [${A}]。`];
		expect(chipNumbers(blocks, env)).toEqual([1, 2, 1, 3, 2]);
	});

	it('表・リスト・引用・強調の中の引用も文書順に拾う', () => {
		const src = `- 項目 [${A}]\n\n| a | b |\n|---|---|\n| **[${B}]** | x |\n\n> 引用 [${FID}]`;
		expect(chipNumbers([src], env)).toEqual([1, 2, 3]);
	});

	it('ストリーミングの途中の部分文字列でも、既に振った番号は変わらない', () => {
		const full = [
			`## 手順 [${A}#Ⅱ．洗浄法]\n\n- 洗浄 [${B}]\n- 登録 [${FID}]\n\n`,
			`出典: [${A}] 原本 [${B}#page=3]、補足 [/ext/cage/doc/x/別の文書]`
		];
		const final = cite(full, env).numbers;
		// 1 文字ずつ伸ばす(先頭ブロックが確定してから次のブロックが伸びる)
		for (let b = 0; b < full.length; b++) {
			for (let i = 0; i <= full[b].length; i++) {
				const blocks = [...full.slice(0, b), full[b].slice(0, i)];
				for (const [key, n] of cite(blocks, env).numbers) {
					expect(final.get(key)).toBe(n);
				}
			}
		}
		expect([...final.values()]).toEqual([1, 2, 3, 4]);
	});

	it('[1] [580] [ラベル](url) は対象外のまま', () => {
		const src =
			'第3条 [1]、表 [580]、[site-a]、[原本](/ext/cage/static/a.pdf)、[/ext/b.pdf](https://e.x)';
		expect(collectCiteRefs(lex(src, env))).toEqual([]);
		expect(cite([src], env).entries).toEqual([]);
	});

	it('実在しない file_id は文字のまま(番号を振らない)', () => {
		const other = '11111111-2222-3333-4444-555555555555';
		const [p] = lex(`[${other}] と [${A}]`, env);
		expect(p.tokens[0].text).toBe(`[${other}] と `);
		expect(chipNumbers([`[${other}] と [${A}]`], env)).toEqual([1]);
		expect(resolveBracketRef(other, env)).toBeNull();
	});
});
