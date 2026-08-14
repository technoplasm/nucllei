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

// run_subagent の usage 要点(BE が prompt_tokens / completion_tokens / duration の
// 数値キーだけを同梱する — nucllei/utils/subagent.py)
export type SubagentUsage = {
	prompt_tokens?: number;
	completion_tokens?: number;
	duration?: number;
};

export type ResultDisplay =
	| { kind: 'text'; value: string }
	| { kind: 'code'; language: string; value: string }
	| { kind: 'json'; value: string }
	// md としてレンダリングして見せる(Code("markdown") = md ソースのハイライトとは別契約)
	| { kind: 'markdown'; value: string }
	// load_skill の構造化結果(#35): value = skill 指示本文(markdown)、tools = 導入 tool 名
	| { kind: 'skill'; value: string; tools: string[] }
	// run_subagent の構造化結果(#107): 子 agent session の要約。sessionId で
	// SubagentSessionBlock が子 transcript を遅延 fetch する。
	| {
			kind: 'subagent';
			sessionId: string;
			ok: boolean;
			answer: string;
			truncated?: boolean;
			error?: string;
			usage?: SubagentUsage;
	  }
	// background 昇格された tool 呼び出しの状態表示(#110)。spawn の即返し
	// {ok, exec_id, tool_name, status: "running"} / check_async / await_async の
	// timeout 形と、結果本文を持たない終端形(failed / stopped / orphaned)。
	// 親メッセージの result は以後不変(append-only)なので、現在の状態は
	// ExecutionBlock が executions store(exec 行ポーリング)から引く。status /
	// error は store に行が無いとき(未 fetch・GC 済み)の拠り所。
	| { kind: 'execution'; execId: string; toolName?: string; status?: string; error?: string };

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

// #110: execution チャンネルの「封筒」— spawn 即返し / check_async / await_async が
// 返す {exec_id, tool_name, status, result?, error?, ...} の共通形。
const EXECUTION_STATUSES = new Set(['running', 'done', 'failed', 'stopped', 'orphaned']);

type ExecutionEnvelope = {
	execId: string;
	toolName?: string;
	status: string;
	error?: string;
	// done の回収形のみ。実 tool の生結果(文字列 or BE が json.loads 済みの object)
	result?: unknown;
};

// 封筒として読めるなら剥がした要素を返す。exec_id ∧ 既知 status の shape で判定する
// (descriptor は封筒ではなく**中身**を指すため、descriptor 分岐より先に効く必要がある)。
const parseExecutionEnvelope = (content: string): ExecutionEnvelope | null => {
	// 生文字列で先に絞り、無関係な結果の JSON.parse を避ける
	if (!content.includes('exec_id')) {
		return null;
	}
	let parsed: unknown;
	try {
		parsed = JSON.parse(content);
	} catch (e) {
		return null;
	}
	if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
		return null;
	}
	const record = parsed as Record<string, unknown>;
	if (typeof record.exec_id !== 'string' || typeof record.status !== 'string') {
		return null;
	}
	if (!EXECUTION_STATUSES.has(record.status)) {
		return null;
	}
	return {
		execId: record.exec_id,
		...(typeof record.tool_name === 'string' ? { toolName: record.tool_name } : {}),
		status: record.status,
		...(typeof record.error === 'string' ? { error: record.error } : {}),
		...(record.result === undefined || record.result === null ? {} : { result: record.result })
	};
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
	// #110: execution の封筒は shape で判定し、descriptor 分岐より**先**に置く。
	// この call に付く descriptor は実 tool(subagent 等)のものであって封筒の形を
	// 表さない — await_async の回収形はここで剥がし、中身に descriptor を適用する。
	const envelope = parseExecutionEnvelope(content);
	if (envelope !== null) {
		if (envelope.status !== 'running' && envelope.result !== undefined) {
			// done の回収形: 封筒を剥がして実 tool の結果として描き直す
			// (BE は results[].render を実 tool の宣言で上書きしてくる — §8)。
			// 中身がさらに封筒でも剥がせるよう再帰する。中身は必ず封筒より短いので
			// 停止する(文字列は JSON が自身を含み得ない)。
			const raw = envelope.result;
			const inner = typeof raw === 'string' ? raw : JSON.stringify(raw);
			// 中身が**文字列**なのに kind=json は、封筒 dict 由来の型 render が
			// そのまま来た(または切詰めで render が降格された)ケース — 実 tool が
			// json を宣言していれば BE は object を返す。宣言なし扱い(= text)に落とす
			const innerDescriptor =
				typeof raw === 'string' && descriptor?.kind === 'json' ? undefined : descriptor;
			return classifyToolResult(inner, innerDescriptor);
		}
		// running(spawn 即返し / check_async / await_async timeout)と、結果本文を
		// 持たない終端形(failed / stopped / orphaned・purge 済み)は状態表示へ。
		// 終端は status / error を持たせる — store に行が無くてもエラーが読める
		return {
			kind: 'execution',
			execId: envelope.execId,
			...(envelope.toolName ? { toolName: envelope.toolName } : {}),
			...(envelope.status === 'running' ? {} : { status: envelope.status }),
			...(envelope.error ? { error: envelope.error } : {})
		};
	}
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
	if (descriptor?.kind === 'subagent') {
		// run_subagent の {ok, session_id, answer, ...}(#107)。
		// spawn 前バリデーションエラー({ok:false, error} — session_id 不在)は形が
		// 合わないので json へ、JSON ですらない壊れ方は text へフォールバック
		// (推測して壊れた表示を作らない)。background 昇格の即返し形は exec 行の
		// running 形(#110)であり、冒頭の execution 分岐が先に受ける。
		try {
			const parsed = JSON.parse(content);
			if (
				parsed !== null &&
				typeof parsed === 'object' &&
				typeof parsed.ok === 'boolean' &&
				typeof parsed.session_id === 'string' &&
				typeof parsed.answer === 'string'
			) {
				// usage は数値キーだけ拾う(型に忠実 — 宣言外の形は捨てる)
				let usage: SubagentUsage | undefined;
				if (parsed.usage !== null && typeof parsed.usage === 'object') {
					usage = {};
					for (const key of ['prompt_tokens', 'completion_tokens', 'duration'] as const) {
						if (typeof parsed.usage[key] === 'number') {
							usage[key] = parsed.usage[key];
						}
					}
					if (Object.keys(usage).length === 0) {
						usage = undefined;
					}
				}
				return {
					kind: 'subagent',
					sessionId: parsed.session_id,
					ok: parsed.ok,
					answer: parsed.answer,
					...(parsed.truncated === true ? { truncated: true } : {}),
					...(typeof parsed.error === 'string' ? { error: parsed.error } : {}),
					...(usage ? { usage } : {})
				};
			}
			if (parsed !== null && typeof parsed === 'object') {
				return { kind: 'json', value: JSON.stringify(parsed, null, 2) };
			}
		} catch (e) {
			// fall through
		}
	}
	if (descriptor?.kind === 'text') {
		return { kind: 'text', value: content };
	}
	if (descriptor?.kind === 'markdown') {
		return { kind: 'markdown', value: content };
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
