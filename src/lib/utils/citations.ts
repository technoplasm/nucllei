// 本文の `[/ext/…]` / `[file_id]` による番号付き引用(#146 の簡易版、2026-10-05)。
//
// - resolveBracketRef: 角括弧の中身 → 引用(CiteRef)。**「本文の ref → 正規化した URL」を決めるのは
//   ここ 1 か所だけ**(markdown-it の inline rule `bracket_ref` がここを呼ぶ)。モデルの写し間違いの
//   自動修正(同じメッセージの tool の結果に近い URL があれば差し替える)を足すならここ。
// - collectCiteRefs: lex 済みトークン(marked 互換形)から、出てきた順に CiteRef を拾う。
// - numberCitations: テキストブロックごとの CiteRef 列(メッセージ内の順)を通しで番号付けする。
//   番号は「ページ」(URL の `#` より前)の初出順。同じページの別の節も同じ番号。
//   出てきた順に振るので、ストリーミングで本文が伸びても既存の番号は変わらない。
import { WEBUI_BASE_URL } from '$lib/constants';

// 対象は中身が次の 2 形のときだけ(`[1]` `[580]` `[site-a]` は対象外):
//   1. `/ext/` で始まるパス(`#見出し` / `#:~:text=引用文` の fragment 可)。doc の住所は半角・全角の
//      空白や括弧を含む(`01_7-1 消毒・滅菌` 等)ので、`]` と改行以外は中身に許す
//   2. file_id(UUID 形)— env.fileNames(そのチャットの添付 id → ファイル名)に在るときだけ
const EXT_REF_RE = /^\/ext\/[^\r\n[\]]+$/;
const FILE_ID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export interface CiteEnv {
	/** そのチャットに実在する添付の id → 表示名(`[file_id]` を引用にする根拠) */
	fileNames?: Map<string, string>;
}

/** 本文の 1 箇所の引用 */
export interface CiteRef {
	kind: 'ext' | 'file';
	/** その箇所のリンク先(fragment 付き、normalize 済み) */
	href: string;
	/** ページのリンク先(`#` より前、normalize 済み) */
	pageHref: string;
	/** 同じ出典の判定キー(ページ単位。decode 済み) */
	key: string;
	/** ページの表示名(末尾の要素 / ファイル名) */
	title: string;
	/** 節の表示名(`見出し` / `「引用文」`)。ページそのものなら null */
	section: string | null;
}

function safeDecode(s: string): string {
	try {
		return decodeURIComponent(s);
	} catch {
		return s;
	}
}

/**
 * Text Fragment(`:~:text=…`)の引用文。モデルは `text=` を落として `#:~:引用文` と書くことが
 * ある(staging 2026-10-06 — ブラウザは `text=` が無いと何もしない)ので、`:~:` で始まれば
 * 引用文とみなす。デコード済みの fragment を受ける。引用文でなければ null。
 */
function textDirective(fragment: string): string | null {
	const m = fragment.match(/^:~:(?:text=)?(.*)$/s);
	return m && m[1] ? m[1] : null;
}

/**
 * 引用文を Text Fragment の値としてエンコードする。encodeURIComponent が残す `-` `,` は
 * 指令の区切り(prefix- / ,textEnd)なので、`&` と合わせて必ず percent-encode する —
 * 空白・`%`・記号を含む引用文でもそのまま効くように。
 */
function encodeTextDirective(text: string): string {
	return encodeURIComponent(text).replace(
		/[-,&]/g,
		(c) => '%' + c.charCodeAt(0).toString(16).toUpperCase().padStart(2, '0')
	);
}

/**
 * 見出しの fragment の先頭のローマ数字の番号(`ⅩⅠ．` `Ⅱ-1 ` 等)を外す。モデルはローマ数字を
 * 写し間違える(staging 2026-10-06: `ⅩⅠ．予防措置と効果` → `Ⅹ．…`、`Ⅹ．…` → `Ⅺ．…`)ので、
 * cage が番号付きの見出しに置く「番号を除いた別名」(llens cage/render.py `heading_aliases`、
 * 番号の形は同ファイルの `_NUM_PREFIX_RE`)に飛ばす。アラビア数字の番号は写し間違えが
 * 見られず、外すと「1. 概要」「2. 概要」のような別名の衝突で飛べなくなりうるので外さない。
 * 番号が無ければそのまま返す。
 */
const ROMAN_PREFIX_RE =
	/^(?:[Ⅰ-Ⅻⅰ-ⅻ]+(?:[.．\-－‐](?:[Ⅰ-Ⅻⅰ-ⅻ]+|[0-9０-９]+))*(?:[.．、:：][\s　]*|[\s　]+))/;

function stripRomanNumber(fragment: string): string {
	const stripped = fragment.replace(ROMAN_PREFIX_RE, '').trim();
	return stripped || fragment;
}

function sectionLabel(fragment: string): string | null {
	if (!fragment) return null;
	// Text Fragment は見出しではなく引用文 — 「…」で見せる
	const text = textDirective(fragment);
	return text !== null ? `「${text}」` : stripRomanNumber(fragment);
}

/**
 * 角括弧の中身 → 引用。対象外なら null。
 * normalizeLink は markdown-it の `md.normalizeLink`(percent-encode)を渡す。
 */
export function resolveBracketRef(
	inner: string,
	env: CiteEnv = {},
	normalizeLink: (url: string) => string = (url) => url
): CiteRef | null {
	if (EXT_REF_RE.test(inner)) {
		const hash = inner.indexOf('#');
		const pageUrl = hash >= 0 ? inner.slice(0, hash) : inner;
		const parts = pageUrl.split('/').filter(Boolean);
		const fragment = hash >= 0 ? safeDecode(inner.slice(hash + 1)) : '';
		const quote = textDirective(fragment);
		// 引用文は `#:~:text=<Text Fragment の規則でエンコード>` に組み直す(`text=` 抜けも直す)。
		// 見出し(`#見出し`)は先頭のローマ数字を外した別名へ(stripRomanNumber)、percent-encode は normalizeLink
		const href =
			quote !== null
				? `${normalizeLink(pageUrl)}#:~:text=${encodeTextDirective(quote)}`
				: fragment
					? normalizeLink(`${pageUrl}#${stripRomanNumber(fragment)}`)
					: normalizeLink(inner);
		return {
			kind: 'ext',
			href,
			pageHref: normalizeLink(pageUrl),
			key: safeDecode(pageUrl),
			title: safeDecode(parts[parts.length - 1] ?? pageUrl),
			section: sectionLabel(fragment)
		};
	}
	if (FILE_ID_RE.test(inner)) {
		const name = env.fileNames?.get(inner);
		if (name === undefined) return null;
		const url = `${WEBUI_BASE_URL}/api/v1/files/${inner}/content`;
		return {
			kind: 'file',
			href: url,
			pageHref: url,
			key: `file:${inner.toLowerCase()}`,
			title: name || inner,
			section: null
		};
	}
	return null;
}

/** `/ext/a/b.md#節` → `b.md › 節`(番号を振らない場所での 1 行表示) */
export function citeLabel(ref: Pick<CiteRef, 'title' | 'section'>): string {
	return ref.section ? `${ref.title} › ${ref.section}` : ref.title;
}

type AnyToken = { type: string; [key: string]: any };

/** lex 済みトークン(marked 互換形)から、文書順に引用を拾う */
export function collectCiteRefs(tokens: AnyToken[] | null | undefined, out: CiteRef[] = []) {
	for (const token of tokens ?? []) {
		if (!token) continue;
		if (token.type === 'link' && token.cite) out.push(token.cite as CiteRef);
		if (Array.isArray(token.tokens)) collectCiteRefs(token.tokens, out);
		if (Array.isArray(token.items)) collectCiteRefs(token.items, out);
		if (token.type === 'table') {
			for (const cell of token.header ?? []) collectCiteRefs(cell?.tokens, out);
			for (const row of token.rows ?? []) {
				for (const cell of row ?? []) collectCiteRefs(cell?.tokens, out);
			}
		}
	}
	return out;
}

export interface CitationSection {
	href: string;
	label: string;
}

export interface CitationEntry {
	n: number;
	kind: CiteRef['kind'];
	key: string;
	title: string;
	pageHref: string;
	/** 本文で使われた節(初出順・重複なし) */
	sections: CitationSection[];
}

export interface Citations {
	/** ページの key → 番号 */
	numbers: Map<string, number>;
	entries: CitationEntry[];
}

/** ブロックごとの引用列(メッセージ内の順)を通しで番号付けする */
export function numberCitations(groups: Iterable<CiteRef[]>): Citations {
	const numbers = new Map<string, number>();
	const entries: CitationEntry[] = [];
	for (const refs of groups) {
		for (const ref of refs) {
			let n = numbers.get(ref.key);
			if (n === undefined) {
				n = entries.length + 1;
				numbers.set(ref.key, n);
				entries.push({
					n,
					kind: ref.kind,
					key: ref.key,
					title: ref.title,
					pageHref: ref.pageHref,
					sections: []
				});
			}
			const entry = entries[n - 1];
			if (ref.section && !entry.sections.some((s) => s.href === ref.href)) {
				entry.sections.push({ href: ref.href, label: ref.section });
			}
		}
	}
	return { numbers, entries };
}
