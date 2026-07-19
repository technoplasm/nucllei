import { describe, it, expect } from 'vitest';
import { lex, render } from './index';

describe('markdown-it lexer (#22 L2)', () => {
	it('setext トラップを解消: 段落 + 次行 --- が heading に化けない', () => {
		// 遡及文法(lheading)が無効なので、これは段落 + hr であって h2 ではない
		const tokens = lex('これは本文\n---\n次の段落');
		expect(tokens.find((t) => t.type === 'heading')).toBeUndefined();
		expect(tokens.some((t) => t.type === 'hr')).toBe(true);
	});

	it('ATX 見出しは heading になる', () => {
		const [h] = lex('## 見出し');
		expect(h.type).toBe('heading');
		expect(h.depth).toBe(2);
		expect(h.tokens[0].text).toBe('見出し');
	});

	it('段落のインライン(strong/em/codespan/link)', () => {
		const [p] = lex('**太字** と *斜体* と `code` と [x](https://e.x)');
		expect(p.type).toBe('paragraph');
		const types = p.tokens.map((t: any) => t.type);
		expect(types).toContain('strong');
		expect(types).toContain('em');
		expect(types).toContain('codespan');
		expect(types).toContain('link');
		const link = p.tokens.find((t: any) => t.type === 'link');
		expect(link.href).toBe('https://e.x');
	});

	it('fence コードブロックは raw に ``` を含む(CodeBlock 描画条件)', () => {
		const [c] = lex('```python\nprint(1)\n```');
		expect(c.type).toBe('code');
		expect(c.lang).toBe('python');
		expect(c.text).toBe('print(1)');
		expect(c.raw.includes('```')).toBe(true);
	});

	it('番号なし/番号付きリストと loose/tight', () => {
		const [tight] = lex('- a\n- b');
		expect(tight.type).toBe('list');
		expect(tight.ordered).toBe(false);
		expect(tight.loose).toBe(false);
		expect(tight.items.length).toBe(2);

		const [loose] = lex('- a\n\n- b');
		expect(loose.loose).toBe(true);

		const [ol] = lex('3. x\n4. y');
		expect(ol.ordered).toBe(true);
		expect(ol.start).toBe(3);
	});

	it('GFM タスクリスト', () => {
		const [list] = lex('- [ ] todo\n- [x] done');
		expect(list.items[0].task).toBe(true);
		expect(list.items[0].checked).toBe(false);
		expect(list.items[1].checked).toBe(true);
	});

	it('テーブル(align 込み)', () => {
		const [t] = lex('| a | b |\n|:--|--:|\n| 1 | 2 |');
		expect(t.type).toBe('table');
		expect(t.header.map((h: any) => h.text)).toEqual(['a', 'b']);
		expect(t.align).toEqual(['left', 'right']);
		expect(t.rows[0].map((c: any) => c.text)).toEqual(['1', '2']);
	});

	it('インライン katex', () => {
		const [p] = lex('式は $E=mc^2$ です');
		const k = p.tokens.find((t: any) => t.type === 'inlineKatex');
		expect(k).toBeTruthy();
		expect(k.text).toBe('E=mc^2');
	});

	it('ブロック katex', () => {
		const tokens = lex('$$\n\\int x dx\n$$');
		expect(tokens.some((t) => t.type === 'blockKatex')).toBe(true);
	});

	it('生 HTML は html トークンとして通す(描画側で DOMPurify)', () => {
		const [p] = lex('段落に <b>html</b> 混入');
		const html = p.tokens.find((t: any) => t.type === 'html');
		expect(html).toBeTruthy();
	});

	it('details 擬似 HTML は互換処理せず html トークンとして素通しする(旧データ互換なし)', () => {
		// 旧フォーマットの読み取りフォールバックは持たない(CLAUDE.md 絶対制約)。
		// <details> は他の生 HTML と同じく html トークン → 描画側 DOMPurify に任せる。
		const tokens = lex('<details type="reasoning" done="true"><summary>x</summary>思考</details>');
		expect(tokens.some((t) => t.type === 'details')).toBe(false);
		expect(tokens.some((t) => t.type === 'html')).toBe(true);
	});

	it('render() は HTML を返し、code fence を hljs でハイライトする', () => {
		const html = render('# 見出し\n\n```json\n{"a": 1}\n```');
		expect(html).toContain('<h1>');
		// hljs のトークン span が入る(clipboard 整形コピー等の highlight 復元)
		expect(html).toContain('hljs-attr');
	});

	it('参照リンク定義は無効(reference disable)', () => {
		// [foo]: url の定義行はリンク解決に使われない → [foo] は link トークンにならない
		const tokens = lex('[foo]\n\n[foo]: https://e.x');
		const hasLink = JSON.stringify(tokens).includes('"type":"link"');
		expect(hasLink).toBe(false);
	});

	describe('リンク属性の出し分け(#87 markdown-it-attrs)', () => {
		const firstLink = (src: string) => {
			const [p] = lex(src);
			return p.tokens.find((t: any) => t.type === 'link');
		};

		it('無指定リンクは target/download とも null(描画側の既定 = _blank)', () => {
			const link = firstLink('[x](https://e.x)');
			expect(link.target).toBeNull();
			expect(link.download).toBeNull();
		});

		it('{target=_self} が搬送される', () => {
			const link = firstLink('[x](/somewhere){target=_self}');
			expect(link.target).toBe('_self');
		});

		it('{download=voice.mp3} が搬送される', () => {
			const link = firstLink('[x](/api/v1/files/abc/content){download=voice.mp3}');
			expect(link.download).toBe('voice.mp3');
		});

		it('裸の {download} はファイル名なしのダウンロード指定(空文字)になる', () => {
			const link = firstLink('[x](/api/v1/files/abc/content){download}');
			expect(link.download).toBe('');
		});

		it('allowlist 外の属性(onclick / class 等)は捨てられる', () => {
			// トークン直描画は DOMPurify を通らないため、lexer 段で落ちることが安全性の根拠
			const [p] = lex('[x](https://e.x){onclick=alert(1) class=evil}');
			const json = JSON.stringify(p);
			expect(json).not.toContain('onclick');
			expect(json).not.toContain('evil');
		});
	});
});
