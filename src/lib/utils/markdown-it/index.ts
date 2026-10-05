// markdown-it ベースの lexer(#22 L2)。
//
// 目的: setext 見出し(遡及文法)を `disable('lheading')` で文法ごと消す。
// marked では tokenizer 差し替えのハックが要るところを 1 行で切れる。
// 参照リンク定義(`[foo]: url`)も同様に disable。
//
// レンダリング層(MarkdownTokens.svelte / MarkdownInlineTokens.svelte)は
// marked のトークン形を消費するため、markdown-it のフラットなトークン列を
// marked 互換のネストツリーへ変換して返す(既存の Svelte 描画・XSS sink を再利用)。
// スキーマの一次情報: _memos/rendering-design.md §3。
import MarkdownIt from 'markdown-it';
import type { Token as MDToken } from 'markdown-it/index.js';
import hljs from 'highlight.js';
import markdownItAttrs from 'markdown-it-attrs';
import katexPlugin from './katex';
import { WEBUI_BASE_URL } from '$lib/constants';

const md = new MarkdownIt({
	html: true, // 生 HTML は html トークンとして通し、描画側で DOMPurify する
	linkify: false, // 裸 URL の自動リンクはしない(marked 既定に寄せる)
	breaks: false,
	typographer: false,
	// render()(HTML 直生成: clipboard 整形コピー・ツールチップ等)の code fence を
	// hljs でハイライトする。lex()(md.parse)経路はトークン content が raw のままで、
	// チャット本文の CodeBlock(CodeMirror)には影響しない。
	highlight: (str, lang) => {
		if (lang && hljs.getLanguage(lang)) {
			try {
				return hljs.highlight(str, { language: lang }).value;
			} catch {
				// fall through to default escaping
			}
		}
		return '';
	}
});

// リンクの属性出し分け(#87): `[t](url){target=_self}` / `[t](url){download=voice.mp3}`。
// トークン直描画(MarkdownInlineTokens)は DOMPurify を通らないため allowlist 必須 —
// target / download 以外の属性(class / onclick 等)は書いても捨てられる。
md.use(markdownItAttrs, { allowedAttributes: ['target', 'download'] });

// 遡及文法の無効化(rendering-design.md §2c)。
// lheading = setext 見出し(段落の次行の ---/=== で遡って h1/h2 になる)。
// reference = `[foo]: url` の参照リンク定義。どちらもストリーミングで壊れる。
md.disable(['lheading', 'reference']);

// リンクによる簡易引用(#146 の簡易版、2026-10-05): 本文中の角括弧 1 つだけの参照を
// リンクにする。対象は中身が次の 2 形のときだけ(それ以外の `[1]` `[580]` `[site-a]` は文字のまま):
//   1. `/ext/` で始まるパス(`#見出し` の fragment 可)— 表示は末尾の要素(+ ` › 見出し`)、
//      リンク先はそのまま(`#見出し` で節に飛ばすのは /ext の配信側の責務)
//   2. file_id(UUID 形)— lex の env.fileNames(そのチャットの添付 id → ファイル名)に
//      在るときだけ。表示はファイル名、リンク先は `/api/v1/files/{id}/content`
// 直後が `(` `[` `:` のもの(`[ラベル](url)` / `[a][b]` / 参照リンク定義 `[x]: url`)は
// 巻き込まない。コード内は backticks / fence が先に取るので対象外。
// doc の住所は半角・全角の空白や括弧を含む(`01_7-1 消毒・滅菌` 等)ので、`]` と改行以外は
// 中身に許す(範囲は角括弧で閉じている。リンク先は normalizeLink が percent-encode する)。
const EXT_REF_RE = /^\/ext\/[^\r\n[\]]+$/;
const FILE_ID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export interface LexEnv {
	/** そのチャットに実在する添付の id → 表示名(`[file_id]` をリンクにする根拠) */
	fileNames?: Map<string, string>;
}

function safeDecode(s: string): string {
	try {
		return decodeURIComponent(s);
	} catch {
		return s;
	}
}

/** `/ext/a/b.md#節` → `b.md › 節`(末尾 "/" のディレクトリは最後の要素) */
export function extRefLabel(ref: string): string {
	const hash = ref.indexOf('#');
	const path = hash >= 0 ? ref.slice(0, hash) : ref;
	const fragment = hash >= 0 ? safeDecode(ref.slice(hash + 1)) : '';
	const parts = path.split('/').filter(Boolean);
	const last = safeDecode(parts[parts.length - 1] ?? path);
	if (!fragment) return last;
	// Text Fragment(`#:~:text=…`)は見出しではなく引用文 — 「…」で見せる
	const text = fragment.match(/^:~:text=(.*)$/);
	return text ? `${last} › 「${text[1]}」` : `${last} › ${fragment}`;
}

md.inline.ruler.before('link', 'bracket_ref', (state, silent) => {
	const src = state.src;
	const start = state.pos;
	if (src.charCodeAt(start) !== 0x5b /* [ */) return false;
	const end = src.indexOf(']', start + 1);
	if (end < 0 || end > state.posMax) return false;
	const inner = src.slice(start + 1, end);
	const next = src[end + 1];
	if (next === '(' || next === '[' || next === ':') return false;

	let href: string;
	let label: string;
	if (EXT_REF_RE.test(inner)) {
		href = state.md.normalizeLink(inner);
		if (!state.md.validateLink(href)) return false;
		label = extRefLabel(inner);
	} else if (FILE_ID_RE.test(inner)) {
		const name = (state.env as LexEnv | undefined)?.fileNames?.get(inner);
		if (name === undefined) return false;
		href = `${WEBUI_BASE_URL}/api/v1/files/${inner}/content`;
		label = name || inner;
	} else {
		return false;
	}

	if (!silent) {
		const open = state.push('link_open', 'a', 1);
		open.attrs = [['href', href]];
		open.markup = 'bracket_ref';
		const text = state.push('text', '', 0);
		text.content = label;
		state.push('link_close', 'a', -1);
	}
	state.pos = end + 1;
	return true;
});

// 旧 details 擬似 HTML(<details type="reasoning"> 等)の互換処理
// (details_block ルーラ + parseDetails)は撤去済み — 旧データ互換を
// 持たない方針(CLAUDE.md 絶対制約、2026-07-14)。reasoning / tool_calls は
// content_blocks(#17 L0)で構造のまま届き、本文に details 擬似 HTML は現れない。

interface AnyToken {
	type: string;
	[key: string]: any;
}

function attr(token: MDToken, name: string): string | null {
	return token.attrGet ? token.attrGet(name) : null;
}

// markdown-it の inline.children を marked 互換のインライントークン列へ変換
function convertInline(children: MDToken[] | null): AnyToken[] {
	if (!children) return [];
	const out: AnyToken[] = [];
	const stack: { type: string; token: AnyToken }[] = [];

	const push = (t: AnyToken) => {
		if (stack.length) {
			stack[stack.length - 1].token.tokens.push(t);
		} else {
			out.push(t);
		}
	};

	for (const tok of children) {
		switch (tok.type) {
			case 'text':
				push({ type: 'text', raw: tok.content, text: tok.content });
				break;
			case 'softbreak':
				push({ type: 'text', raw: '\n', text: '\n' });
				break;
			case 'hardbreak':
				push({ type: 'br' });
				break;
			case 'code_inline':
				push({ type: 'codespan', text: tok.content, raw: '`' + tok.content + '`' });
				break;
			case 'strong_open':
				stack.push({ type: 'strong', token: { type: 'strong', tokens: [] } });
				break;
			case 'em_open':
				stack.push({ type: 'em', token: { type: 'em', tokens: [] } });
				break;
			case 's_open':
				stack.push({ type: 'del', token: { type: 'del', tokens: [] } });
				break;
			case 'strong_close':
			case 'em_close':
			case 's_close': {
				const done = stack.pop();
				if (done) push(done.token);
				break;
			}
			case 'link_open':
				stack.push({
					type: 'link',
					token: {
						type: 'link',
						href: attr(tok, 'href') ?? '',
						title: attr(tok, 'title'),
						// markdown-it-attrs 経由(#87)。無指定は null(描画側の既定 = _blank)
						target: attr(tok, 'target'),
						download: attr(tok, 'download'),
						tokens: []
					}
				});
				break;
			case 'link_close': {
				const done = stack.pop();
				if (done) push(done.token);
				break;
			}
			case 'image':
				push({
					type: 'image',
					href: attr(tok, 'src') ?? '',
					text: tok.content ?? '',
					tokens: convertInline(tok.children)
				});
				break;
			case 'html_inline':
				push({ type: 'html', text: tok.content, raw: tok.content });
				break;
			case 'katex_inline':
				push({ type: 'inlineKatex', text: tok.content, displayMode: false });
				break;
			default:
				// 未対応 inline は素通し(テキスト化)
				if (tok.content) push({ type: 'text', raw: tok.content, text: tok.content });
		}
	}
	// 閉じ損ねた open は中身をそのまま出す
	while (stack.length) {
		const done = stack.pop()!;
		push(done.token);
	}
	return out;
}

// markdown-it のブロックトークン列(フラット)を marked 互換ツリーへ。
// cursor を進めながら *_open ... *_close を再帰的に畳む。
function convertBlocks(tokens: MDToken[], start = 0, stopType: string | null = null) {
	const out: AnyToken[] = [];
	let i = start;
	while (i < tokens.length) {
		const tok = tokens[i];
		if (stopType && tok.type === stopType) {
			return { tokens: out, next: i };
		}
		switch (tok.type) {
			case 'heading_open': {
				const inline = tokens[i + 1];
				out.push({
					type: 'heading',
					depth: Number(tok.tag.slice(1)) || 1,
					tokens: convertInline(inline?.children ?? [])
				});
				i += 3; // open, inline, close
				break;
			}
			case 'paragraph_open': {
				const inline = tokens[i + 1];
				out.push({ type: 'paragraph', tokens: convertInline(inline?.children ?? []) });
				i += 3;
				break;
			}
			case 'fence': {
				const lang = (tok.info || '').trim().split(/\s+/)[0] ?? '';
				out.push({
					type: 'code',
					lang,
					text: tok.content.replace(/\n$/, ''),
					raw: '```' + (tok.info || '') + '\n' + tok.content + '```'
				});
				i += 1;
				break;
			}
			case 'code_block': {
				// インデントコードブロック。raw に ``` を含めないので描画側は素の text 扱い
				out.push({
					type: 'code',
					lang: '',
					text: tok.content.replace(/\n$/, ''),
					raw: tok.content
				});
				i += 1;
				break;
			}
			case 'hr':
				out.push({ type: 'hr' });
				i += 1;
				break;
			case 'html_block': {
				out.push({ type: 'html', text: tok.content, raw: tok.content });
				i += 1;
				break;
			}
			case 'katex_block':
				out.push({ type: 'blockKatex', text: tok.content, displayMode: true });
				i += 1;
				break;
			case 'blockquote_open': {
				const inner = convertBlocks(tokens, i + 1, 'blockquote_close');
				out.push({
					type: 'blockquote',
					tokens: inner.tokens,
					text: blockquoteRawText(inner.tokens)
				});
				i = inner.next + 1;
				break;
			}
			case 'bullet_list_open':
			case 'ordered_list_open': {
				const ordered = tok.type === 'ordered_list_open';
				const list = convertList(tokens, i, ordered);
				out.push(list.token);
				i = list.next + 1;
				break;
			}
			case 'table_open': {
				const table = convertTable(tokens, i);
				out.push(table.token);
				i = table.next + 1;
				break;
			}
			case 'inline': {
				out.push({ type: 'paragraph', tokens: convertInline(tok.children ?? []) });
				i += 1;
				break;
			}
			default:
				i += 1;
		}
	}
	return { tokens: out, next: i };
}

// blockquote 先頭の [!NOTE] 等を AlertRenderer が拾えるよう素テキストを復元
function blockquoteRawText(tokens: AnyToken[]): string {
	const first = tokens[0];
	if (first?.type === 'paragraph' && first.tokens?.[0]?.type === 'text') {
		return first.tokens[0].raw ?? '';
	}
	return '';
}

function convertList(tokens: MDToken[], start: number, ordered: boolean) {
	const open = tokens[start];
	const startAttr = attr(open, 'start');
	const items: AnyToken[] = [];
	let loose = false;
	let i = start + 1;
	const closeType = ordered ? 'ordered_list_close' : 'bullet_list_close';

	while (i < tokens.length && tokens[i].type !== closeType) {
		if (tokens[i].type === 'list_item_open') {
			// tight リストでは paragraph_open が hidden、loose では出る
			const inner = convertBlocks(tokens, i + 1, 'list_item_close');
			// loose 判定: item 内の paragraph が hidden でないか
			for (let j = i + 1; j < inner.next; j++) {
				if (tokens[j].type === 'paragraph_open' && !tokens[j].hidden) {
					loose = true;
					break;
				}
			}
			const item = buildListItem(inner.tokens);
			items.push(item);
			i = inner.next + 1;
		} else {
			i += 1;
		}
	}
	return {
		token: {
			type: 'list',
			ordered,
			start: startAttr ? Number(startAttr) : ordered ? 1 : '',
			loose,
			items
		},
		next: i
	};
}

// GFM タスクリスト([ ] / [x])を検出して task/checked を立てる
function buildListItem(tokens: AnyToken[]): AnyToken {
	const item: AnyToken = { type: 'list_item', tokens, task: false, checked: false };
	const firstInline =
		tokens[0]?.type === 'paragraph' || tokens[0]?.type === 'text' ? tokens[0].tokens : null;
	const firstText = firstInline?.[0];
	if (firstText?.type === 'text' && typeof firstText.raw === 'string') {
		const m = firstText.raw.match(/^\[( |x|X)\]\s+/);
		if (m) {
			item.task = true;
			item.checked = m[1].toLowerCase() === 'x';
			firstText.raw = firstText.raw.slice(m[0].length);
			firstText.text = firstText.raw;
		}
	}
	return item;
}

function convertTable(tokens: MDToken[], start: number) {
	const header: any[] = [];
	const rows: any[][] = [];
	const align: (string | null)[] = [];
	let i = start + 1;
	let inBody = false;

	while (i < tokens.length && tokens[i].type !== 'table_close') {
		const tok = tokens[i];
		if (tok.type === 'thead_open') {
			inBody = false;
		} else if (tok.type === 'tbody_open') {
			inBody = true;
		} else if (tok.type === 'tr_open') {
			const row: any[] = [];
			i += 1;
			while (i < tokens.length && tokens[i].type !== 'tr_close') {
				const cellOpen = tokens[i];
				if (cellOpen.type === 'th_open' || cellOpen.type === 'td_open') {
					const style = attr(cellOpen, 'style') ?? '';
					const a = style.includes('right')
						? 'right'
						: style.includes('center')
							? 'center'
							: style.includes('left')
								? 'left'
								: null;
					const inline = tokens[i + 1];
					const cell = {
						text: inline?.content ?? '',
						tokens: convertInline(inline?.children ?? [])
					};
					if (inBody) {
						row.push(cell);
					} else {
						header.push(cell);
						align.push(a);
					}
					i += 3; // cell_open, inline, cell_close
				} else {
					i += 1;
				}
			}
			if (inBody) rows.push(row);
		}
		i += 1;
	}
	return {
		token: { type: 'table', header, rows, align },
		next: i
	};
}

katexPlugin(md);

// md.render(HTML 直生成)が katex トークンで落ちないよう、素テキストを出す。
// KatexRenderer は Svelte 側専用なので、文字列レンダリング経路では数式本文をそのまま出す。
md.renderer.rules.katex_inline = (tokens, idx) => tokens[idx].content;
md.renderer.rules.katex_block = (tokens, idx) => tokens[idx].content + '\n';

/**
 * marked.lexer(src) 互換の入口。marked 形のトークン配列を返す。
 * MarkdownTokens.svelte / MarkdownInlineTokens.svelte がそのまま消費できる。
 * env.fileNames を渡すと `[file_id]` がリンクになる(上の bracket_ref)。
 */
export function lex(src: string, env: LexEnv = {}): AnyToken[] {
	const mdTokens = md.parse(src ?? '', env);
	return convertBlocks(mdTokens).tokens;
}

/**
 * marked.parse(src) 互換の入口。HTML 文字列を返す(ツールチップ・説明・バナー等)。
 * 呼び出し側で必要に応じて DOMPurify する規律は従来どおり。
 */
export function render(src: string): string {
	return md.render(src ?? '');
}

export default md;
