// katex を markdown-it のトークンとして扱うプラグイン(#22 L2)。
// 区切りと検出ロジックは marked 版(utils/marked/katex-extension.ts)から移植し、
// 挙動を揃える。実レンダリングは KatexRenderer.svelte 側で行うため、
// ここでは content(数式本文)と displayMode を持つトークンを積むだけ。
import type MarkdownIt from 'markdown-it';

const DELIMITER_LIST = [
	{ left: '$$', right: '$$', display: true },
	{ left: '$', right: '$', display: false },
	{ left: '\\pu{', right: '}', display: false },
	{ left: '\\ce{', right: '}', display: false },
	{ left: '\\(', right: '\\)', display: false },
	{ left: '\\[', right: '\\]', display: true },
	{ left: '\\begin{equation}', right: '\\end{equation}', display: true }
];

function escapeRegex(string: string) {
	return string.replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&');
}

const inlinePatterns: string[] = [];
const blockPatterns: string[] = [];

for (const { left, right, display } of DELIMITER_LIST) {
	const escapedLeft = escapeRegex(left);
	const escapedRight = escapeRegex(right);
	if (!display) {
		inlinePatterns.push(`${escapedLeft}((?:\\\\[^]|[^\\\\])+?)${escapedRight}`);
	} else {
		inlinePatterns.push(`${escapedLeft}(?!\\n)((?:\\\\[^]|[^\\\\])+?)(?!\\n)${escapedRight}`);
		blockPatterns.push(`${escapedLeft}\\n((?:\\\\[^]|[^\\\\])+?)\\n${escapedRight}`);
	}
}

// 先頭アンカー版(現在位置から一致するか)。数式末尾は特殊文字で終われる。
const inlineRule = new RegExp(`^(${inlinePatterns.join('|')})(?=[\\s?。，!-\/:-@[-\`{-~]|$)`, 'u');
const blockRule = new RegExp(`^(${blockPatterns.join('|')})(?=[\\s?。，!-\/:-@[-\`{-~]|$)`, 'u');

function extractText(match: RegExpMatchArray): string {
	return (
		match
			.slice(2)
			.filter((item) => item)
			.find((item) => item.trim()) ?? ''
	);
}

export default function katexPlugin(md: MarkdownIt) {
	// インライン: $...$, $$...$$(改行なし), \(...\), \pu{}, \ce{} など
	md.inline.ruler.after('escape', 'katex_inline', (state, silent) => {
		const start = state.pos;
		// marked 版と同じ「直前が特殊文字 or 行頭」の制約
		if (start > 0) {
			const prev = state.src.charAt(start - 1);
			if (!/[\s?。，!-\/:-@[-`{-~]/.test(prev)) return false;
		}
		const match = state.src.slice(start).match(inlineRule);
		if (!match) return false;
		const text = extractText(match);
		if (!text.trim()) return false;
		if (!silent) {
			const token = state.push('katex_inline', 'span', 0);
			token.content = text;
			token.markup = match[0];
		}
		state.pos += match[0].length;
		return true;
	});

	// ブロック: 独立行の $$ ... $$ / \[ ... \] / \begin{equation} ... \end{equation}
	md.block.ruler.before('fence', 'katex_block', (state, startLine, endLine, silent) => {
		const begin = state.bMarks[startLine] + state.tShift[startLine];
		const rest = state.src.slice(begin);
		const match = rest.match(blockRule);
		if (!match) return false;
		const raw = match[0];
		// 消費した行数を数える(raw 内の改行数 + 開始行)
		const consumedLines = raw.split('\n').length;
		if (startLine + consumedLines - 1 >= endLine + 1) {
			// はみ出す場合でも silent でなければ受理を試みる
		}
		if (silent) return true;
		const token = state.push('katex_block', 'div', 0);
		token.block = true;
		token.content = extractText(match);
		token.markup = raw;
		token.map = [startLine, startLine + consumedLines];
		state.line = startLine + consumedLines;
		return true;
	});
}
