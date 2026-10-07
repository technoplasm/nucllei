// tool 引数/結果の型駆動レンダリング(issue #20)の純ロジック。
// 仕様: _memos/tool-arg-render.md。引数はバックエンドが calls[].render.args に同梱した、
// 結果はバックエンドが実行後に確定して results[].render に載せた(#153)
// 正規化ディスクリプタ {kind, language} の通りに描画内容を決める。
// フロントは dumb — 引数名・値の長さからの推測はしない。
// フォールバックは「型に忠実」(素の str は scalar、object/array は json)。

import { parsePartialArguments } from './partial';

export type RenderDescriptor = {
	kind: string;
	language?: string;
};

// call に付くのは引数の表示だけ(結果の表示は results[].render — #153)
export type ToolCallRender = {
	args?: Record<string, RenderDescriptor>;
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
	// {ok, task_id, tool_name, status: "running"} が対象。親メッセージの result は
	// 以後不変(append-only)なので、現在の状態は ExecutionBlock が executions store
	// (exec 行ポーリング)から引く。回収(await_task)は {"tasks":[...]} 形で届き、
	// ここではなく tasks 封筒の剥がし(classifyToolResult)が扱う。
	// FE 内部の変数名は execId のまま(asyncio の task_id と取り違えないための境界)
	| { kind: 'execution'; execId: string; toolName?: string };

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

// tool のエラー種別(BE の ToolError — README_API §5.1)。tool result / exec 行の
// `error_kind` でサーバが明示する。表示はこの旗だけで決める — 本文("ERROR: …")を
// 文字列として解釈しない。**赤はバグ・致命的なものとモデルの規約違反だけ**、ふつうの
// 失敗は amber(2026-10-04 ken):
// - call: tool の中で見つけた呼び方の誤り(値の形・id)→ 呼び直し(amber)
// - failure: 実行時の失敗(timeout・到達不能・上流のエラー・上限)→ 失敗(amber)
// - outcome: 呼び出しは成立、答えが「無い」(404・範囲外・該当なし)→ 該当なし(amber)
// - violation: モデルが呼び出しの規約を破った(一覧に無い tool・読めない arguments・
//   必須引数の欠落・署名に無い引数)→ 規約違反(red)
// - fatal: tool / サーバ自体が壊れている(設定・バグ・捕らえていない例外)→ エラー(red)
export const TOOL_ERROR_KINDS = ['call', 'failure', 'outcome', 'violation', 'fatal'] as const;
export type ToolErrorKind = (typeof TOOL_ERROR_KINDS)[number];

export const toolErrorKind = (value: unknown): ToolErrorKind | null =>
	(TOOL_ERROR_KINDS as readonly unknown[]).includes(value) ? (value as ToolErrorKind) : null;

// 種別ごとの見た目。icon / 色のクラスは Tailwind が拾えるよう文字列リテラルのまま持つ
// (組み立てない)。amber の 3 種は同じ色調でアイコンだけ変える。ラベル(呼び直し / 失敗 /
// 該当なし / 規約違反 / エラー)は i18next-parser が拾える .svelte 側
// (`components/chat/Messages/ToolErrorLabel.svelte`)が持つ
export type ToolErrorLook = {
	icon: string;
	// ヘッダのアイコン・Result タブの矢印(図形)
	iconClass: string;
	// ラベル文字(図形より 1 段濃い — 細い文字でも読めるように)
	textClass: string;
};

const AMBER_ICON = 'text-amber-500 dark:text-amber-400';
const AMBER_TEXT = 'text-amber-600 dark:text-amber-400';
const RED_ICON = 'text-red-600 dark:text-red-400';
const RED_TEXT = 'text-red-600 dark:text-red-400';

export const TOOL_ERROR_LOOKS: Record<ToolErrorKind, ToolErrorLook> = {
	call: {
		icon: 'i-lucide-rotate-ccw',
		iconClass: AMBER_ICON,
		textClass: AMBER_TEXT
	},
	failure: {
		icon: 'i-lucide-circle-alert',
		iconClass: AMBER_ICON,
		textClass: AMBER_TEXT
	},
	outcome: {
		icon: 'i-lucide-search-x',
		iconClass: AMBER_ICON,
		textClass: AMBER_TEXT
	},
	violation: {
		icon: 'i-lucide-ban',
		iconClass: RED_ICON,
		textClass: RED_TEXT
	},
	fatal: {
		icon: 'i-lucide-circle-x',
		iconClass: RED_ICON,
		textClass: RED_TEXT
	}
};

// exec 行(background)の終端 → 見た目の種別。done で届く種別は raise の outcome と、
// 結果に付けた旗(BE の tool_output.mark_error_kind — 例: 実行した Python が失敗した
// run_python。結果があるので done)。それ以外の raise は failed で届く。failed なのに
// 種別が無い行(spawn の後段で起動できなかった行 — 同じターンの結果は failure)は failure の
// 見た目にする(赤はサーバが fatal と言ったときだけ)
export const executionErrorKind = (
	status: string | null | undefined,
	value: unknown
): ToolErrorKind | null => {
	const kind = toolErrorKind(value);
	if (status === 'done') return kind;
	if (status === 'failed') return kind && kind !== 'outcome' ? kind : 'failure';
	return null;
};

// #110: execution チャンネルの「封筒」2 種。
// ① spawn 即返し {ok, task_id, tool_name, status: "running"} — kind=execution の
//    状態表示へ(現在状態は exec 行ポーリングが持つ)。
// ② await_task の返り {"tasks":[ENTRY...]} — 要求 id がちょうど 1 件 ∧ done のとき、
//    BE は results[].render を実 tool の確定済みの表示(text を含む)に差し替えてくる
//    (#153)ので、中身(ENTRY.result)をその descriptor で描き直す。
//    複数件・running(時間切れ)・terminal 非 done は剥がさず通常描画(JSON のまま)。
//    check_task / stop_task も {"tasks":[...]} だが結果本文(result)を持たないため
//    剥がし条件に合わず、自然に通常描画になる。

type SpawnEnvelope = {
	execId: string;
	toolName?: string;
};

// spawn 即返し形として読めるなら剥がした要素を返す。task_id ∧ status running の
// shape で判定する(descriptor は封筒ではなく実 tool を指すため、descriptor 分岐より
// 先に効く必要がある)。
const parseSpawnEnvelope = (content: string): SpawnEnvelope | null => {
	// 生文字列で先に絞り、無関係な結果の JSON.parse を避ける
	if (!content.includes('task_id')) {
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
	if (typeof record.task_id !== 'string' || record.status !== 'running') {
		return null;
	}
	return {
		execId: record.task_id,
		...(typeof record.tool_name === 'string' ? { toolName: record.tool_name } : {})
	};
};

// await_task の {"tasks":[...]} から「1 件 ∧ done ∧ result あり」の中身を取り出す。
// 条件に合わない形(複数件・running・result 無し・tasks 以外)は null。
const parseSingleDoneTaskResult = (content: string): unknown | undefined => {
	if (!content.includes('task_id')) {
		return undefined;
	}
	let parsed: unknown;
	try {
		parsed = JSON.parse(content);
	} catch (e) {
		return undefined;
	}
	if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
		return undefined;
	}
	const tasks = (parsed as Record<string, unknown>).tasks;
	if (!Array.isArray(tasks) || tasks.length !== 1) {
		return undefined;
	}
	const entry = tasks[0];
	if (entry === null || typeof entry !== 'object' || Array.isArray(entry)) {
		return undefined;
	}
	const record = entry as Record<string, unknown>;
	if (typeof record.task_id !== 'string' || record.status !== 'done') {
		return undefined;
	}
	if (record.result === undefined || record.result === null) {
		return undefined;
	}
	return record.result;
};

// tool 結果(文字列)をディスクリプタで出し分ける。ディスクリプタは backend が実行後に
// 優先順(エラー・切詰め → text、Rendered、戻り型宣言、dict/list → json)で 1 つに確定した
// results[].render だけ(#153 — tool_render.resolve_result_render)。無ければ text —
// 中身を見て JSON かどうかを推測する動的解釈はしない(型に忠実の原則。
// 2026-07-17 改訂 — 旧実装は非 JSON 文字列をクォート表示、JSON 風文字列を
// 勝手に整形していた)。tool の結果は「値を見る」ものでありデザイン対象では
// ないため、table 描画経路は持たない(未知 kind も text に落とす)。
export const classifyToolResult = (
	content: string,
	descriptor?: RenderDescriptor
): ResultDisplay => {
	// #110: execution の封筒は shape で判定し、descriptor 分岐より**先**に置く。
	// spawn 即返しは状態表示(kind=execution)へ。
	const spawn = parseSpawnEnvelope(content);
	if (spawn !== null) {
		return {
			kind: 'execution',
			execId: spawn.execId,
			...(spawn.toolName ? { toolName: spawn.toolName } : {})
		};
	}
	// await_task の回収形 {"tasks":[...]}: 要求 id がちょうど 1 件 ∧ done なら剥がし、
	// 中身に descriptor を適用する(BE がこの条件のときだけ results[].render を実 tool の
	// 確定済みの表示に差し替えてくる)。複数件・running・非 done は剥がさない — 封筒の
	// json のまま通常描画。
	if (descriptor !== undefined) {
		const raw = parseSingleDoneTaskResult(content);
		if (raw !== undefined) {
			// 中身がさらに封筒でも剥がせるよう再帰する。中身は必ず封筒より短いので
			// 停止する(文字列は JSON が自身を含み得ない)。
			const inner = typeof raw === 'string' ? raw : JSON.stringify(raw);
			return classifyToolResult(inner, descriptor);
		}
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
