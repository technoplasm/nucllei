import { describe, it, expect } from 'vitest';
import { lex, render, type LexEnv } from './index';

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

describe('リンクによる簡易引用 [/ext/…] / [file_id](#146 の簡易版)', () => {
	const FID = '0b6c8c0e-1d2f-4a3b-9c8d-7e6f5a4b3c2d';
	const env: LexEnv = { fileNames: new Map([[FID, '手順書.pdf']]) };
	const inline = (src: string, e: LexEnv = env) => lex(src, e)[0].tokens;
	const links = (src: string, e: LexEnv = env) =>
		inline(src, e).filter((t: any) => t.type === 'link');

	it('[/ext/…] はリンク。表示は末尾の要素、fragment は › で続ける', () => {
		const [a, b] = links(
			'根拠 [/ext/cage/doc/院内マニュアル/医療安全管理マニュアル#医療安全] と [/ext/cage/static/x/15-7.pdf]'
		);
		expect(a.href).toBe(
			'/ext/cage/doc/' +
				encodeURIComponent('院内マニュアル') +
				'/' +
				encodeURIComponent('医療安全管理マニュアル') +
				'#' +
				encodeURIComponent('医療安全')
		);
		expect(a.tokens[0].text).toBe('医療安全管理マニュアル › 医療安全');
		expect(a.target).toBeNull(); // 描画側の既定 = 新しいタブ(_blank)
		expect(b.href).toBe('/ext/cage/static/x/15-7.pdf');
		expect(b.tokens[0].text).toBe('15-7.pdf');
	});

	it('doc の住所の空白・全角空白・括弧・・・_ を含んでもリンク。直後の全角「（」で切れる', () => {
		const cases = [
			'/ext/cage/doc/院内マニュアル/電話番号簿/01_職員PHS/03_中央診療施設等/08_病理部・病理診断科',
			'/ext/cage/doc/院内マニュアル/感染対策マニュアル/01_7-1 消毒・滅菌',
			'/ext/cage/doc/院内マニュアル/薬剤/018_凍結乾燥製剤(溶解剤付き)　調製'
		];
		for (const ref of cases) {
			const toks = inline(`[${ref}]（院内PHS電話番号簿）`);
			const [a] = toks.filter((t: any) => t.type === 'link');
			expect(a.href).toBe(
				ref
					.split('/')
					.map((p) => encodeURIComponent(p).replace(/%28/g, '(').replace(/%29/g, ')'))
					.join('/')
			);
			expect(a.tokens[0].text).toBe(ref.split('/').pop());
			expect(toks[toks.length - 1].text).toBe('（院内PHS電話番号簿）');
		}
		// /ext/ で始まらない doc の住所は対象外(文字のまま)
		expect(links('[/院内マニュアル/電話番号簿/01_職員PHS]')).toHaveLength(0);
	});

	it('Text Fragment(#:~:text=…)は引用文として「」で見せる', () => {
		const [a] = links('[/ext/cage/doc/a/01_転落・転倒防止対策#:~:text=再発の防止]');
		expect(a.tokens[0].text).toBe('01_転落・転倒防止対策 › 「再発の防止」');
	});

	it('percent-encoded の住所も表示はデコードする', () => {
		const [a] = links('[/ext/a/%E5%9B%B3.png]');
		expect(a.href).toBe('/ext/a/%E5%9B%B3.png');
		expect(a.tokens[0].text).toBe('図.png');
	});

	it('[file_id] はそのチャットの添付に在るときだけリンク(表示はファイル名)', () => {
		const [a] = links(`添付 [${FID}] を参照`);
		expect(a.href).toBe(`/api/v1/files/${FID}/content`);
		expect(a.tokens[0].text).toBe('手順書.pdf');
		// 不在の id・env 無しは文字のまま
		const other = 'ffffffff-1d2f-4a3b-9c8d-7e6f5a4b3c2d';
		expect(links(`[${other}]`)).toHaveLength(0);
		expect(links(`[${FID}]`, {})).toHaveLength(0);
		expect(
			inline(`[${other}]`)
				.map((t: any) => t.text)
				.join('')
		).toBe(`[${other}]`);
	});

	it('[1] [580] [site-a] は文字のまま', () => {
		const toks = inline('第3条 [1]、表 [580]、[site-a]。');
		expect(toks.some((t: any) => t.type === 'link')).toBe(false);
		expect(toks.map((t: any) => t.text).join('')).toBe('第3条 [1]、表 [580]、[site-a]。');
	});

	it('[ラベル](url) は普通のリンクのまま(直後の "(" は巻き込まない)', () => {
		const [a] = links('[/ext/a.pdf](https://e.x/b.pdf)');
		expect(a.href).toBe('https://e.x/b.pdf');
		expect(a.tokens[0].text).toBe('/ext/a.pdf');
		const [b] = links(`[${FID}](https://e.x/c)`);
		expect(b.href).toBe('https://e.x/c');
	});

	it('参照リンク定義 [x]: url と [a][b] は巻き込まない', () => {
		expect(links('[/ext/a.pdf]: https://e.x')).toHaveLength(0);
		expect(links(`[${FID}]: https://e.x`)).toHaveLength(0);
		expect(links('[/ext/a.pdf][x]')).toHaveLength(0);
	});

	it('コード内は対象外', () => {
		const [p] = lex(`\`[/ext/a.pdf]\` と \`[${FID}]\``, env);
		expect(p.tokens.some((t: any) => t.type === 'link')).toBe(false);
		const [c] = lex('```\n[/ext/a.pdf]\n```', env);
		expect(c.type).toBe('code');
		expect(c.text).toBe('[/ext/a.pdf]');
	});

	it('/ext/ で始まらないもの・改行をまたぐものは対象外', () => {
		expect(links('[/ext/a\nb.pdf]')).toHaveLength(0);
		expect(links('[/extra/a.pdf]')).toHaveLength(0);
		expect(links('[ext/a.pdf]')).toHaveLength(0);
	});
});
