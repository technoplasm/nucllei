// tool 引数/結果の型駆動レンダリング(issue #20)の純ロジック。
// 仕様: _memos/tool-arg-render.md。バックエンドが calls[].render に同梱した
// 正規化ディスクリプタ {kind, language} の通りに描画内容を決める。
// フロントは dumb — 引数名・値の長さからの推測はしない。
// フォールバックは「型に忠実」(素の str は scalar、object/array は json)。

export type RenderDescriptor = {
	kind: string;
	language?: string;
};

export type ToolCallRender = {
	args?: Record<string, RenderDescriptor>;
	result?: RenderDescriptor;
};

export type ArgDisplay = {
	name: string;
	kind: 'code' | 'scalar' | 'json';
	language?: string;
	// code/scalar は生の値(JSON.parse がアンエスケープ済み)、json は整形済み文字列
	value: string;
};

// モデル由来の arguments(JSON 文字列)をディスクリプタ駆動の表示リストに変換する。
// パース不能・object 以外(モデルが壊れた JSON を吐いた等)は null を返し、
// 呼び出し側は従来の一枚岩 JSON 表示にフォールバックする。
export const parseToolArguments = (
	argsJson: string,
	render?: Record<string, RenderDescriptor>
): ArgDisplay[] | null => {
	let parsed: unknown;
	try {
		parsed = JSON.parse(argsJson);
	} catch (e) {
		return null;
	}
	if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
		return null;
	}

	return Object.entries(parsed as Record<string, unknown>).map(([name, value]) => {
		const descriptor = render?.[name];
		// code 宣言は値が実際に文字列のときだけ効く(型に忠実 — 宣言と実値が
		// 食い違ったら推測せず構造どおりに落とす)
		if (descriptor?.kind === 'code' && typeof value === 'string') {
			return {
				name,
				kind: 'code' as const,
				language: descriptor.language || 'plaintext',
				value
			};
		}
		if (value === null || ['string', 'number', 'boolean'].includes(typeof value)) {
			return {
				name,
				kind: 'scalar' as const,
				value: typeof value === 'string' ? value : JSON.stringify(value)
			};
		}
		return { name, kind: 'json' as const, value: JSON.stringify(value, null, 2) };
	});
};

export type ResultDisplay =
	| { kind: 'text'; value: string }
	| { kind: 'code'; language: string; value: string }
	| { kind: 'json'; value: string }
	| { kind: 'table'; columns: string[]; rows: string[][] };

// 文字列化された JSON を再帰的に剥がして整形する(旧 Collapsible 実装と同じ挙動。
// tool 結果は dict/list が json.dumps 済み文字列で届くため)。
const parseJSONString = (str: unknown): unknown => {
	try {
		return parseJSONString(JSON.parse(str as string));
	} catch (e) {
		return str;
	}
};

export const formatJSONString = (str: unknown): string => {
	const parsed = parseJSONString(str);
	if (parsed !== null && typeof parsed === 'object') {
		return JSON.stringify(parsed, null, 2);
	}
	// 数値・真偽値などのプリミティブは文字列表現に
	return `${JSON.stringify(String(parsed))}`;
};

// 表として描けるか: 非空の「プレーン object の配列」のみ。列は出現順の和集合。
const tryParseTable = (content: string): { columns: string[]; rows: string[][] } | null => {
	let parsed: unknown;
	try {
		parsed = JSON.parse(content);
	} catch (e) {
		return null;
	}
	if (
		!Array.isArray(parsed) ||
		parsed.length === 0 ||
		!parsed.every((row) => row !== null && typeof row === 'object' && !Array.isArray(row))
	) {
		return null;
	}

	const columns: string[] = [];
	for (const row of parsed as Record<string, unknown>[]) {
		for (const key of Object.keys(row)) {
			if (!columns.includes(key)) {
				columns.push(key);
			}
		}
	}
	const rows = (parsed as Record<string, unknown>[]).map((row) =>
		columns.map((column) => {
			const value = row[column];
			if (value === undefined || value === null) {
				return '';
			}
			return typeof value === 'object' ? JSON.stringify(value) : String(value);
		})
	);
	return { columns, rows };
};

// tool 結果(文字列)を戻り型ディスクリプタで出し分ける。宣言が無ければ従来
// どおり「JSON なら整形、そうでなければそのまま」の json 表示。table 宣言でも
// 実値が表形でなければ json に落とす(推測して壊れた表を作らない)。
export const classifyToolResult = (
	content: string,
	descriptor?: RenderDescriptor
): ResultDisplay => {
	if (descriptor?.kind === 'text') {
		return { kind: 'text', value: content };
	}
	if (descriptor?.kind === 'code') {
		return { kind: 'code', language: descriptor.language || 'plaintext', value: content };
	}
	if (descriptor?.kind === 'table') {
		const table = tryParseTable(content);
		if (table) {
			return { kind: 'table', ...table };
		}
	}
	return { kind: 'json', value: formatJSONString(content) };
};
