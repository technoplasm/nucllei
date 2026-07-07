/**
 * ストリーミング・ブロックセグメンタ (L1) — issue #21 / #14
 *
 * 「伸びる markdown 文字列」を「確定ブロック列 (stable) + 末尾の未確定部分 (tail)」に分割する
 * 純ロジック。戦略 (b) ブロック凍結型インクリメンタル(_memos/rendering-design.md 参照)。
 * 後続で markdown-it (#22) が確定ブロックを個別にパースし、tail だけを毎フレーム再パースする。
 *
 * ── 絶対条件: 追記単調性 ───────────────────────────────────────────────
 *   任意の文字列 s と延長 s+t について、segmentMarkdown(s).stable は
 *   segmentMarkdown(s+t).stable の先頭部分列(element-wise prefix)である。
 *   = 一度「確定」したブロックは、その後どんな続きが来ても変わらない。
 *
 * ── 確定判定(保守的。迷ったら確定しない)─────────────────────────────
 *   確定してよいのは「もう続きが来ても意味が変わらないと証明できた」ブロックだけ。
 *
 *   - 段落など(paragraph / 見出し / 引用 / 表 / thematic break):
 *       直後に「完全な空行」(改行が打ち終わった空行)が来たら確定。
 *       空行は setext 見出し (`---`/`===`) と表区切り (`|---|`) の遡及を遮断する。
 *       末尾の未完行の直後や単一改行だけでは確定しない(次に `---` が来て見出しに化けうる)。
 *   - fenced code (``` / ~~~):
 *       閉じフェンス行が「完全な行」として現れるまで確定しない。フェンスは空行を跨ぐ。
 *   - HTML ブロック type 1-5 (`<script>`/`<pre>`/`<style>`/`<textarea>` / `<!--` /
 *       `<?` / `<!X` / `<![CDATA[`):
 *       これらは空行を跨ぎ専用の終端条件で閉じる。終端行が完全に現れるまで確定しない。
 *       (type 6/7 は空行で閉じるので paragraph と同じ扱いにフォールバックする)
 *   - list / indented code block:
 *       空行を挟んでも lazy continuation / loose list / インデント継続で後続行が
 *       前のブロックに吸われうる。よって「後続に別の(非インデントで list marker でない)
 *       ブロックが現れて終端が証明される」まで確定しない。これらが末尾ブロックの間は保留。
 *
 * ── 意図的に扱わないもの ───────────────────────────────────────────────
 *   - 参照リンク定義 `[foo]: url`:
 *       文書末尾の定義がインライン `[foo]` の解釈を遡って変える文書全体スコープの文法。
 *       セグメンタでは通常の段落として扱い、遡及を考慮しない。
 *       #22 で markdown-it 側の該当ルール(reference)を disable する前提。ここでは無視する。
 *   - setext 見出しそのもの:
 *       上記のとおり「空行で確定を遮断する」ことで安全側に倒すだけで、
 *       setext を正しく見出しに畳む責務は持たない(#22 の markdown-it が担当。
 *       運用上は lheading を disable する想定)。
 *
 * 依存追加なし・純 TS。UI 統合(#22)はここでは行わない。
 */

export interface SegmenterResult {
	/** 確定ブロック(markdown ソース片)。一度確定したら追記で不変。 */
	stable: string[];
	/** 末尾の未確定部分(表示用に毎フレーム再パースされる想定の生ソース)。 */
	tail: string;
}

type BlockType = 'paragraph' | 'fence' | 'html' | 'list' | 'indented';

interface Block {
	start: number; // 開始行 index(inclusive)
	end: number; // 終了行 index(exclusive)
	type: BlockType;
	closed: boolean; // fence / html の終端が現れたか
}

// ── 行レベルの述語 ─────────────────────────────────────────────────────

const BLANK_RE = /^[ \t\r]*$/;

function isBlank(line: string): boolean {
	return BLANK_RE.test(line);
}

/** 先頭インデント幅(タブは 4 桁タブストップ換算)。 */
function indentWidth(line: string): number {
	let w = 0;
	for (const ch of line) {
		if (ch === ' ') w += 1;
		else if (ch === '\t') w += 4 - (w % 4);
		else break;
	}
	return w;
}

interface FenceMarker {
	char: string;
	len: number;
}

/** 開きフェンスなら {char,len} を返す。バッククォート系は info string に ` を含むと無効。 */
function fenceStart(line: string): FenceMarker | null {
	const m = line.match(/^ {0,3}(`{3,}|~{3,})(.*)$/);
	if (!m) return null;
	const marker = m[1];
	const char = marker[0];
	if (char === '`' && m[2].includes('`')) return null;
	return { char, len: marker.length };
}

/** open フェンスに対応する閉じフェンス行か(同種・同長以上・他文字なし)。 */
function fenceClose(line: string, open: FenceMarker): boolean {
	const m = line.match(/^ {0,3}(`{3,}|~{3,})[ \t\r]*$/);
	if (!m) return false;
	const marker = m[1];
	return marker[0] === open.char && marker.length >= open.len;
}

function isListStart(line: string): boolean {
	return /^ {0,3}([-+*])([ \t]|$)/.test(line) || /^ {0,3}\d{1,9}[.)]([ \t]|$)/.test(line);
}

// 空行を跨ぐ HTML ブロック(CommonMark type 1-5)。type 6/7 は空行で閉じるため扱わない。
interface HtmlType {
	start: RegExp;
	end: RegExp;
}
const HTML_TYPES: HtmlType[] = [
	{ start: /^<(script|pre|style|textarea)(\s|>|$)/i, end: /<\/(script|pre|style|textarea)>/i },
	{ start: /^<!--/, end: /-->/ },
	{ start: /^<\?/, end: /\?>/ },
	{ start: /^<![A-Za-z]/, end: />/ },
	{ start: /^<!\[CDATA\[/, end: /\]\]>/ },
	// llens: reasoning / tool_calls の <details ...> は本文に空行を含みうる。
	// CommonMark 既定(type 6)は空行で閉じるため、そのままだと details が分断され
	// 閉じ `</details>` が孤立して本文にリテラル表示される(#22 移行以来の潜在バグ)。type 1-5 と
	// 同じ「終端タグまで空行を跨ぐ」扱いにして details 全体を 1 ブロックに保つ。
	{ start: /^<details(\s|>|$)/i, end: /<\/details>/i }
];

function stripLead(line: string): string {
	return line.replace(/^ {0,3}/, '');
}

function htmlBlockStart(line: string): HtmlType | null {
	if (indentWidth(line) >= 4) return null;
	const s = stripLead(line);
	for (const t of HTML_TYPES) {
		if (t.start.test(s)) return t;
	}
	return null;
}

// ── トークナイズ:行配列をブロック列へ ────────────────────────────────

function tokenize(lines: string[]): Block[] {
	const blocks: Block[] = [];
	const n = lines.length;
	let i = 0;

	while (i < n) {
		// ブロック間の空行(セパレータ)を読み飛ばす
		while (i < n && isBlank(lines[i])) i++;
		if (i >= n) break;

		const start = i;
		const line = lines[i];

		// 1) fenced code block
		const fence = fenceStart(line);
		if (fence) {
			i++;
			while (i < n && !fenceClose(lines[i], fence)) i++;
			const closed = i < n;
			if (closed) i++;
			blocks.push({ start, end: i, type: 'fence', closed });
			continue;
		}

		// 2) HTML ブロック type 1-5(空行を跨ぐ)
		const html = htmlBlockStart(line);
		if (html) {
			let closed = html.end.test(stripLead(line));
			i++;
			if (!closed) {
				while (i < n && !html.end.test(lines[i])) i++;
				closed = i < n;
				if (closed) i++;
			}
			blocks.push({ start, end: i, type: 'html', closed });
			continue;
		}

		// 3) indented code block(先頭ブロックが indent>=4)
		if (indentWidth(line) >= 4) {
			i = consumeIndented(lines, i);
			blocks.push({ start, end: i, type: 'indented', closed: true });
			continue;
		}

		// 4) list
		if (isListStart(line)) {
			i = consumeList(lines, i);
			blocks.push({ start, end: i, type: 'list', closed: true });
			continue;
		}

		// 5) paragraph 的(見出し / 引用 / 表 / thematic break 含む)
		i++;
		while (i < n) {
			const l = lines[i];
			if (isBlank(l)) break;
			// 空行を跨ぐ構造(フェンス / HTML ブロック)は段落から切り離し専用処理へ
			if (fenceStart(l) || htmlBlockStart(l)) break;
			i++;
		}
		blocks.push({ start, end: i, type: 'paragraph', closed: true });
	}

	return blocks;
}

/** indented code block を消費(空行は後続も indent>=4 の間だけ内包)。 */
function consumeIndented(lines: string[], i: number): number {
	const n = lines.length;
	i++;
	while (i < n) {
		if (isBlank(lines[i])) {
			let j = i;
			while (j < n && isBlank(lines[j])) j++;
			if (j < n && indentWidth(lines[j]) >= 4) {
				i = j;
				continue;
			}
			return i; // 空行止まり(内包しない)
		}
		if (indentWidth(lines[i]) >= 4) {
			i++;
			continue;
		}
		return i; // 非インデント行で終端
	}
	return i;
}

/**
 * list を消費。
 * - 空行なしの後続非空行 = lazy continuation / ネスト内容として内包。
 * - 空行の後:後続が indent>=2 または list marker なら loose list として内包、
 *   非インデントかつ非 marker なら list 終端(その行から次ブロック)。
 */
function consumeList(lines: string[], i: number): number {
	const n = lines.length;
	i++;
	while (i < n) {
		if (isBlank(lines[i])) {
			let j = i;
			while (j < n && isBlank(lines[j])) j++;
			if (j >= n) return i; // 末尾の空行:内包しない(まだ継続しうるので終端未証明)
			if (indentWidth(lines[j]) >= 2 || isListStart(lines[j])) {
				i = j;
				continue;
			}
			return i; // 空行 + 非インデント非 marker 行 → list 終端
		}
		i++; // lazy continuation
	}
	return i;
}

// ── 確定判定 ──────────────────────────────────────────────────────────

/**
 * 行 index k が「完全な行」(直後に改行が確定している行)か。
 * lines は source.split('\n') なので最後の要素は「まだ改行の来ていない現在行」。
 */
function isCompleteLine(k: number, total: number): boolean {
	return k < total - 1;
}

export function segmentMarkdown(source: string): SegmenterResult {
	const lines = source.split('\n');
	const total = lines.length;
	const blocks = tokenize(lines);

	const stable: string[] = [];
	let confirmedEnd = 0;

	for (let k = 0; k < blocks.length; k++) {
		const b = blocks[k];
		let ok = false;

		switch (b.type) {
			case 'fence':
			case 'html':
				// 終端行が完全に現れていれば確定(以後変化しない)
				ok = b.closed && b.end < total;
				break;
			case 'list':
			case 'indented':
				// 終端を証明する後続ブロックが存在する場合のみ確定(末尾では保留)
				ok = k < blocks.length - 1;
				break;
			case 'paragraph':
			default:
				// 直後が「完全な空行」なら確定(setext / 表区切りの遡及を遮断)
				ok = b.end < total && isBlank(lines[b.end]) && isCompleteLine(b.end, total);
				break;
		}

		if (!ok) break; // 未確定に当たったら以降は tail へ(連続性・単調性の保証)

		stable.push(lines.slice(b.start, b.end).join('\n'));
		confirmedEnd = b.end;
	}

	const tail = lines.slice(confirmedEnd).join('\n');
	return { stable, tail };
}

// ── fence 仮閉じ(#22 の表示用)────────────────────────────────────────

/**
 * tail に未クローズの fenced code block が含まれる場合、表示用に仮想的に閉じた
 * 文字列を返す。フェンス内では他種のフェンスマーカーは content 扱い(同種・同長以上の
 * 行だけが閉じる)という入れ子規則に従う。閉じ済み・フェンス無しならそのまま返す。
 */
export function closeUnclosedFences(tail: string): string {
	const lines = tail.split('\n');
	let open: FenceMarker | null = null;

	for (const line of lines) {
		if (open) {
			if (fenceClose(line, open)) open = null;
		} else {
			const f = fenceStart(line);
			if (f) open = f;
		}
	}

	if (!open) return tail;

	const closing = open.char.repeat(open.len);
	// 末尾が改行で終わっていなければ改行を足してから閉じフェンスを付ける
	return tail.endsWith('\n') ? tail + closing : tail + '\n' + closing;
}

// ── 逐次 push 型ラッパ(任意・利便のみ。正は segmentMarkdown)──────────

/**
 * 追記のたびに segmentMarkdown を呼ぶ薄いラッパ。純関数と結果は完全に一致する。
 * newlyStable には「今回新たに確定したブロック」だけが入る(DOM 追記に使える)。
 */
export class StreamingSegmenter {
	private source = '';
	private stableCount = 0;

	push(chunk: string): SegmenterResult & { newlyStable: string[] } {
		this.source += chunk;
		const res = segmentMarkdown(this.source);
		const newlyStable = res.stable.slice(this.stableCount);
		this.stableCount = res.stable.length;
		return { ...res, newlyStable };
	}

	reset(): void {
		this.source = '';
		this.stableCount = 0;
	}

	get value(): string {
		return this.source;
	}
}
