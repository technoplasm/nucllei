// 未閉鎖(ストリーミング途中)の tool 引数 JSON の寛容パーサ(issue #20 ステップ5)。
//
// モデルが流す arguments はチャンク任意位置で切れる(実キャプチャの実態):
// - 切断はほぼ常に文字列値の内部(tool_call_long_code: 129/129)
// - エスケープシーケンスが割れる(`…\` で切れて次チャンクが `n…`)
// - キー途中・値開始前でも切れる
//
// 方針: 累積文字列を毎フレーム先頭から再スキャンするステートレス関数
// (引数は高々数 KB、O(n) 再走査で十分。フレーム間状態を持たない)。
// 末尾の不完全エスケープ(孤立 `\`・`\uXX` 途中)は出力に含めず「保留」し、
// 次フレームで完全になってから現れる。

export type PartialArgEntry = {
	name: string;
	// isString=true: アンエスケープ済みの(部分)文字列値
	// isString=false: number/bool/null/object/array の生 JSON 断片
	value: string;
	isString: boolean;
	complete: boolean;
};

export type PartialParseResult = {
	entries: PartialArgEntry[];
	// 終端の } まで読めた(オブジェクト全体が閉じた)
	done: boolean;
};

const WHITESPACE = ' \t\n\r';

const ESCAPE_MAP: Record<string, string> = {
	'"': '"',
	'\\': '\\',
	'/': '/',
	b: '\b',
	f: '\f',
	n: '\n',
	r: '\r',
	t: '\t'
};

// 引数オブジェクトを先頭から寛容にスキャンする。
// 先頭が `{` でない(モデルが object 以外を吐いた)場合は null —
// 呼び出し側は従来の一枚岩表示にフォールバックする。空文字列は「まだ何も
// 届いていない」として空の結果を返す。
export const parsePartialArguments = (src: string): PartialParseResult | null => {
	const n = src.length;
	let i = 0;

	const skipWhitespace = () => {
		while (i < n && WHITESPACE.includes(src[i])) i++;
	};

	// 開始 `"` の次の位置から文字列を読み、デコード済みテキストを返す。
	// 末尾で切れた場合 complete=false(不完全なエスケープは text に含めない)。
	const readString = (): { text: string; complete: boolean } => {
		let text = '';
		while (i < n) {
			const ch = src[i];
			if (ch === '"') {
				i++;
				return { text, complete: true };
			}
			if (ch === '\\') {
				if (i + 1 >= n) {
					// 孤立した \(エスケープが次チャンクに割れている)→ 保留
					i = n;
					return { text, complete: false };
				}
				const esc = src[i + 1];
				if (esc === 'u') {
					if (i + 6 > n) {
						// \uXXXX の途中 → 保留
						i = n;
						return { text, complete: false };
					}
					const hex = src.slice(i + 2, i + 6);
					text += /^[0-9a-fA-F]{4}$/.test(hex)
						? String.fromCharCode(parseInt(hex, 16))
						: '\\u' + hex;
					i += 6;
				} else {
					text += ESCAPE_MAP[esc] ?? '\\' + esc;
					i += 2;
				}
			} else {
				text += ch;
				i++;
			}
		}
		return { text, complete: false };
	};

	// 非文字列値(number/true/false/null/object/array)を生テキストで読む。
	const readNonString = (): { raw: string; complete: boolean } => {
		const start = i;
		if (src[i] === '{' || src[i] === '[') {
			let depth = 0;
			let inString = false;
			while (i < n) {
				const ch = src[i];
				if (inString) {
					if (ch === '\\') {
						i += 2;
						continue;
					}
					if (ch === '"') inString = false;
					i++;
					continue;
				}
				if (ch === '"') {
					inString = true;
				} else if (ch === '{' || ch === '[') {
					depth++;
				} else if (ch === '}' || ch === ']') {
					depth--;
					if (depth === 0) {
						i++;
						return { raw: src.slice(start, i), complete: true };
					}
				}
				i++;
			}
			return { raw: src.slice(start, i), complete: false };
		}
		// number / true / false / null: デリミタか空白まで。入力末尾で切れて
		// いたら streaming 中(値が伸びる可能性がある)
		while (i < n && !',}'.includes(src[i]) && !WHITESPACE.includes(src[i])) i++;
		return { raw: src.slice(start, i), complete: i < n };
	};

	skipWhitespace();
	if (i >= n) return { entries: [], done: false };
	if (src[i] !== '{') return null;
	i++;

	const entries: PartialArgEntry[] = [];
	let done = false;

	while (true) {
		skipWhitespace();
		if (i >= n) break;
		if (src[i] === '}') {
			done = true;
			break;
		}
		if (src[i] === ',') {
			i++;
			continue;
		}
		if (src[i] !== '"') break; // 想定外の構文 → ここまでで打ち切り
		i++;

		const key = readString();
		// キー途中・`:` 未着・値未開始で切れている間は、値の型が未確定なので
		// エントリを出さない(code → json のちらつきを避ける)
		if (!key.complete) break;
		skipWhitespace();
		if (i >= n || src[i] !== ':') break;
		i++;
		skipWhitespace();
		if (i >= n) break;

		if (src[i] === '"') {
			i++;
			const value = readString();
			entries.push({
				name: key.text,
				value: value.text,
				isString: true,
				complete: value.complete
			});
			if (!value.complete) break;
		} else {
			const value = readNonString();
			entries.push({
				name: key.text,
				value: value.raw,
				isString: false,
				complete: value.complete
			});
			if (!value.complete) break;
		}
	}

	return { entries, done };
};
