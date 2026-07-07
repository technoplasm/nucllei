import { describe, it, expect } from 'vitest';
import { lex, render } from './index';

describe('markdown-it lexer (#22 L2)', () => {
	it('setext トラップを解消: 段落 + 次行 --- が heading に化けない', () => {
		// 遡及文法(lheading)が無効なので、これは段落 + hr であって h2 ではない
		const tokens = lex('これは本文\n---\n次の段落');
		expect(tokens.find((t) => t.type === 'heading')).toBeUndefined();
		expect(tokens.some((t) => t.type === 'hr')).toBe(true);
	});

	it('setext join 境界: </details> 直後の --- が heading を飲み込まない(#14 受け入れ基準)', () => {
		// 旧 prod の実害(serialize が </details> の直後に裸の --- を並べ、marked の
		// lheading が直前段落まで h2 に飲み込む)を再現。L0 で details 直列化は廃止済み
		// だが、レガシー本文に details が残るケースでも lheading 無効で heading 化しない。
		const src =
			'<details type="reasoning" done="true"><summary>x</summary>思考</details>\n\n本文段落\n---\n次の段落';
		const tokens = lex(src);
		expect(tokens.some((t) => t.type === 'heading')).toBe(false);
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

	it('レガシー <details> を details トークンへ復元', () => {
		const [d] = lex(
			'<details type="reasoning" done="true" duration="2">\n<summary>Thought for 2 seconds</summary>\n本文\n</details>'
		);
		expect(d.type).toBe('details');
		expect(d.summary).toBe('Thought for 2 seconds');
		expect(d.attributes.type).toBe('reasoning');
		expect(d.attributes.duration).toBe('2');
	});

	it('#48 回帰: 本文に空行を含む <details> でも </details> が漏れない', () => {
		// CommonMark 既定では <details> は HTML block type 6(空行で閉じる)なので、
		// reasoning 本文に空行があると details が分断され末尾 </details> が孤立して
		// リテラル表示された。details_block ルールで空行を跨ぎ 1 ブロックに保つ。
		const tokens = lex(
			'<details type="reasoning" done="true" duration="2">\n<summary>Thought for 2 seconds</summary>\n最初の思考。\n\n二段落目の思考。\n</details>\n\n答えの本文です。'
		);
		const d = tokens.find((t) => t.type === 'details');
		expect(d).toBeDefined();
		// reasoning 本文は空行込みで details 内に保持される
		expect(d.text).toContain('最初の思考。');
		expect(d.text).toContain('二段落目の思考。');
		// 孤立した </details> の html トークンが本文に残らない
		const leaked = JSON.stringify(tokens).includes('</details>');
		expect(leaked).toBe(false);
		// 回答本文は details の外に段落として存在する
		expect(tokens.some((t) => t.type === 'paragraph')).toBe(true);
	});

	it('#48 回帰: 閉じ未着(ストリーミング途中)の <details> も畳める', () => {
		const tokens = lex(
			'<details type="reasoning" done="false">\n<summary>Thinking…</summary>\n途中まで考えて\n\nまだ続く'
		);
		const d = tokens.find((t) => t.type === 'details');
		expect(d).toBeDefined();
		expect(d.attributes.done).toBe('false');
		expect(JSON.stringify(tokens).includes('<details')).toBe(false);
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
});
