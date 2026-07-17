// tool 引数/結果の型駆動レンダリング(issue #20)の純ロジック。
// 仕様: _memos/tool-arg-render.md。バックエンドが calls[].render に同梱した
// 正規化ディスクリプタ {kind, language} の通りに描画内容を決める。
// フロントは dumb — 引数名・値の長さからの推測はしない。
// フォールバックは「型に忠実」(素の str は scalar、object/array は json)。

import { parsePartialArguments } from './partial';

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
	// ストリーミング途中(値がまだ伸びる可能性がある)。確定済みは undefined
	streaming?: boolean;
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

// ストリーミング対応版: 完全な JSON はこれまで通り(型に忠実な確定表示)、
// 未閉鎖なら寛容パーサ(partial.ts)で部分値を表示する。object 以外を吐く
// 壊れ方のときだけ null(呼び出し側が一枚岩表示にフォールバック)。
export const parseToolArgumentsStreaming = (
	argsJson: string,
	render?: Record<string, RenderDescriptor>
): ArgDisplay[] | null => {
	const complete = parseToolArguments(argsJson, render);
	if (complete !== null) {
		return complete;
	}

	const partial = parsePartialArguments(argsJson);
	if (partial === null) {
		return null;
	}

	return partial.entries.map((entry) => {
		const descriptor = render?.[entry.name];
		const streaming = entry.complete ? {} : { streaming: true };
		if (entry.isString) {
			if (descriptor?.kind === 'code') {
				return {
					name: entry.name,
					kind: 'code' as const,
					language: descriptor.language || 'plaintext',
					value: entry.value,
					...streaming
				};
			}
			return { name: entry.name, kind: 'scalar' as const, value: entry.value, ...streaming };
		}
		// 非文字列: 確定していれば型に忠実に(scalar/json)、途中なら生断片を json 扱いで
		if (entry.complete) {
			try {
				const value = JSON.parse(entry.value);
				if (value !== null && typeof value === 'object') {
					return {
						name: entry.name,
						kind: 'json' as const,
						value: JSON.stringify(value, null, 2)
					};
				}
				return { name: entry.name, kind: 'scalar' as const, value: entry.value };
			} catch (e) {
				return { name: entry.name, kind: 'json' as const, value: entry.value };
			}
		}
		return { name: entry.name, kind: 'json' as const, value: entry.value, ...streaming };
	});
};

export type ResultDisplay =
	| { kind: 'text'; value: string }
	| { kind: 'code'; language: string; value: string }
	| { kind: 'json'; value: string }
	// load_skill の構造化結果(#35): value = skill 指示本文(markdown)、tools = 導入 tool 名
	| { kind: 'skill'; value: string; tools: string[] };

// 文字列化された JSON を再帰的に剥がす(tool 結果は dict/list が json.dumps 済み
// 文字列で届くため)。剥がすのは文字列層のみ — 旧実装はスカラー到達後も再帰し、
// スタックオーバーフローを catch して止まっていた。
export const parseJSONString = (str: unknown): unknown => {
	if (typeof str !== 'string') {
		return str;
	}
	try {
		return parseJSONString(JSON.parse(str));
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

// tool 結果(文字列)をディスクリプタで出し分ける。ディスクリプタは
// ①戻り型宣言(calls[].render.result)または ②backend が実行時の実型から
// 同梱したもの(dict/list → {kind:"json"})。どちらも無ければ text —
// 中身を見て JSON かどうかを推測する動的解釈はしない(型に忠実の原則。
// 2026-07-17 改訂 — 旧実装は非 JSON 文字列をクォート表示、JSON 風文字列を
// 勝手に整形していた)。tool の結果は「値を見る」ものでありデザイン対象では
// ないため、table 描画経路は持たない(未知 kind も text に落とす)。
export const classifyToolResult = (
	content: string,
	descriptor?: RenderDescriptor
): ResultDisplay => {
	if (descriptor?.kind === 'skill') {
		// load_skill の {result, tools}(#35)。形が合わなければ json フォールバック
		// (推測して壊れた表示を作らない — 他 kind と同じ規律)。
		try {
			const parsed = JSON.parse(content);
			if (
				parsed &&
				typeof parsed.result === 'string' &&
				Array.isArray(parsed.tools) &&
				parsed.tools.every((t: unknown) => typeof t === 'string')
			) {
				return { kind: 'skill', value: parsed.result, tools: parsed.tools };
			}
		} catch (e) {
			// fall through
		}
	}
	if (descriptor?.kind === 'text') {
		return { kind: 'text', value: content };
	}
	if (descriptor?.kind === 'code') {
		return { kind: 'code', language: descriptor.language || 'plaintext', value: content };
	}
	if (descriptor?.kind === 'json') {
		// backend が実型(dict/list)から同梱。content は json.dumps 済み文字列
		return { kind: 'json', value: formatJSONString(content) };
	}
	// ディスクリプタなし・未知 kind: 文字列をそのまま出す(動的解釈しない)
	return { kind: 'text', value: content };
};
