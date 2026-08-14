// tool 引数/結果の型駆動レンダリング(issue #20)のテスト。
// 仕様: _memos/tool-arg-render.md — 名前・長さで推測しない、フォールバックは型に忠実。

import { describe, expect, it } from 'vitest';

import {
	classifyToolResult,
	formatJSONString,
	parseToolArguments,
	parseToolArgumentsStreaming
} from './index';

// tool_call_long_code 実キャプチャ相当: エスケープ済み JSON 文字列の code 引数
const LONG_CODE_ARGS = JSON.stringify({
	code: 'def fizzbuzz(n: int) -> str:\n    if n % 15 == 0:\n        return "FizzBuzz"\n    return str(n)'
});

describe('parseToolArguments', () => {
	it('code 宣言された引数はアンエスケープ済みの code 表示になる(R3)', () => {
		const displays = parseToolArguments(LONG_CODE_ARGS, {
			code: { kind: 'code', language: 'python' }
		});
		expect(displays).toHaveLength(1);
		expect(displays![0]).toEqual({
			name: 'code',
			kind: 'code',
			language: 'python',
			value:
				'def fizzbuzz(n: int) -> str:\n    if n % 15 == 0:\n        return "FizzBuzz"\n    return str(n)'
		});
		// JSON.parse がエスケープを剥がしている(\n が実改行、\" が ")
		expect(displays![0].value).toContain('\n');
		expect(displays![0].value).toContain('"FizzBuzz"');
	});

	it('複数引数が共存する(code + scalar、R4)', () => {
		const args = JSON.stringify({ sql: 'SELECT 1;', limit: 10, verbose: true });
		const displays = parseToolArguments(args, { sql: { kind: 'code', language: 'sql' } });
		expect(displays).toEqual([
			{ name: 'sql', kind: 'code', language: 'sql', value: 'SELECT 1;' },
			{ name: 'limit', kind: 'scalar', value: '10' },
			{ name: 'verbose', kind: 'scalar', value: 'true' }
		]);
	});

	it('宣言が無ければ型に忠実(str は scalar、object は json — code に推測しない)', () => {
		const args = JSON.stringify({
			code: 'print(1)', // 名前が code でも宣言が無ければただの文字列
			filters: { city: '札幌' }
		});
		const displays = parseToolArguments(args, undefined);
		expect(displays).toEqual([
			{ name: 'code', kind: 'scalar', value: 'print(1)' },
			{ name: 'filters', kind: 'json', value: JSON.stringify({ city: '札幌' }, null, 2) }
		]);
	});

	it('code 宣言でも実値が文字列でなければ型に忠実に落とす', () => {
		const args = JSON.stringify({ code: 42 });
		const displays = parseToolArguments(args, { code: { kind: 'code', language: 'python' } });
		expect(displays).toEqual([{ name: 'code', kind: 'scalar', value: '42' }]);
	});

	it('language 欠落は plaintext に落とす', () => {
		const displays = parseToolArguments(JSON.stringify({ code: 'x' }), {
			code: { kind: 'code' }
		});
		expect(displays![0]).toMatchObject({ kind: 'code', language: 'plaintext' });
	});

	it('空引数 {} は空リスト(引数なしツール)', () => {
		expect(parseToolArguments('{}', undefined)).toEqual([]);
	});

	it('パース不能・object 以外は null(呼び出し側が一枚岩表示にフォールバック)', () => {
		expect(parseToolArguments('{"code": "def', undefined)).toBeNull();
		expect(parseToolArguments('"just a string"', undefined)).toBeNull();
		expect(parseToolArguments('[1, 2]', undefined)).toBeNull();
		expect(parseToolArguments('null', undefined)).toBeNull();
	});
});

describe('parseToolArgumentsStreaming', () => {
	const RENDER = { code: { kind: 'code', language: 'python' } };

	it('完全な JSON は parseToolArguments と同一(streaming フラグなし)', () => {
		const args = JSON.stringify({ code: 'print(1)', timeout: 30 });
		expect(parseToolArgumentsStreaming(args, RENDER)).toEqual(parseToolArguments(args, RENDER));
	});

	it('未閉鎖 JSON: code 宣言の部分値が streaming 付き code 表示になる', () => {
		const displays = parseToolArgumentsStreaming('{"code":"def f():\\n    pa', RENDER);
		expect(displays).toEqual([
			{
				name: 'code',
				kind: 'code',
				language: 'python',
				value: 'def f():\n    pa',
				streaming: true
			}
		]);
	});

	it('確定した引数と伸びている引数が共存する', () => {
		const displays = parseToolArgumentsStreaming('{"code":"print(1)","note":"実行し', RENDER);
		expect(displays).toEqual([
			{ name: 'code', kind: 'code', language: 'python', value: 'print(1)' },
			{ name: 'note', kind: 'scalar', value: '実行し', streaming: true }
		]);
	});

	it('伸びている非文字列値は生断片の json 表示(streaming)', () => {
		const displays = parseToolArgumentsStreaming('{"filters":{"city":"札', undefined);
		expect(displays).toEqual([
			{ name: 'filters', kind: 'json', value: '{"city":"札', streaming: true }
		]);
	});

	it('まだ何も届いていない("" / 途中のキー)は空リスト', () => {
		expect(parseToolArgumentsStreaming('', RENDER)).toEqual([]);
		expect(parseToolArgumentsStreaming('{"co', RENDER)).toEqual([]);
	});

	it('object 以外の壊れ方は null(一枚岩フォールバック)', () => {
		expect(parseToolArgumentsStreaming('[1,2', undefined)).toBeNull();
	});
});

describe('classifyToolResult', () => {
	it('json ディスクリプタ(backend が dict/list の実型から同梱)は整形して json 表示', () => {
		const display = classifyToolResult('{"ok": true}', { kind: 'json' });
		expect(display).toEqual({ kind: 'json', value: JSON.stringify({ ok: true }, null, 2) });
		expect(classifyToolResult('[1, 2]', { kind: 'json' })).toEqual({
			kind: 'json',
			value: JSON.stringify([1, 2], null, 2)
		});
	});

	it('ディスクリプタなしは text — JSON に見えても中身から動的解釈しない', () => {
		expect(classifyToolResult('晴れ', undefined)).toEqual({ kind: 'text', value: '晴れ' });
		expect(classifyToolResult('line 1\nline 2', undefined)).toEqual({
			kind: 'text',
			value: 'line 1\nline 2'
		});
		// str が JSON 風でも str は str(推測しない)
		expect(classifyToolResult('{"ok": true}', undefined)).toEqual({
			kind: 'text',
			value: '{"ok": true}'
		});
		// エラー経路(str(e) / "tool not available: ..." 等)も素で読める
		expect(classifyToolResult('tool not available: nope', undefined)).toEqual({
			kind: 'text',
			value: 'tool not available: nope'
		});
	});

	it('markdown 宣言は md レンダリング表示(Code("markdown") のソース表示とは別契約)', () => {
		expect(classifyToolResult('# 見出し\n\n- item', { kind: 'markdown' })).toEqual({
			kind: 'markdown',
			value: '# 見出し\n\n- item'
		});
	});

	it('text 宣言はそのまま(pre-wrap テキスト)', () => {
		const display = classifyToolResult('stdout line 1\nstdout line 2', { kind: 'text' });
		expect(display).toEqual({ kind: 'text', value: 'stdout line 1\nstdout line 2' });
	});

	it('code 宣言は言語付き code 表示', () => {
		const display = classifyToolResult('print(1)', { kind: 'code', language: 'python' });
		expect(display).toEqual({ kind: 'code', language: 'python', value: 'print(1)' });
	});

	it('未知の kind(旧 table 等)は text に落ちる(解釈しない)', () => {
		// table 描画は #60 で廃止済み・backend も宣言を出さない。未知 kind の
		// ディスクリプタが来ても壊れないことだけ担保する。
		expect(classifyToolResult('{"error": "boom"}', { kind: 'table' }).kind).toBe('text');
		expect(classifyToolResult('not json', { kind: 'table' }).kind).toBe('text');
	});

	it('skill 宣言: {result, tools} を分解する(#35)', () => {
		const content = JSON.stringify({
			result: '# 指示\n以降は関西弁で。',
			tools: ['secret_password']
		});
		expect(classifyToolResult(content, { kind: 'skill' })).toEqual({
			kind: 'skill',
			value: '# 指示\n以降は関西弁で。',
			tools: ['secret_password']
		});
	});

	it('subagent 宣言: {ok, session_id, answer, ...} を分解する(#107)', () => {
		const content = JSON.stringify({
			ok: true,
			session_id: 'child-chat-1',
			answer: '調査結果は以下の通りです。',
			usage: { prompt_tokens: 1200, completion_tokens: 340, duration: 12.3 }
		});
		expect(classifyToolResult(content, { kind: 'subagent' })).toEqual({
			kind: 'subagent',
			sessionId: 'child-chat-1',
			ok: true,
			answer: '調査結果は以下の通りです。',
			usage: { prompt_tokens: 1200, completion_tokens: 340, duration: 12.3 }
		});
	});

	it('subagent 宣言: error / truncated が付いた結果も透過する(#107)', () => {
		const content = JSON.stringify({
			ok: false,
			session_id: 'child-chat-2',
			answer: '途中までの回答',
			truncated: true,
			error: 'agent session は停止されました(回答は途中まで)。'
		});
		expect(classifyToolResult(content, { kind: 'subagent' })).toEqual({
			kind: 'subagent',
			sessionId: 'child-chat-2',
			ok: false,
			answer: '途中までの回答',
			truncated: true,
			error: 'agent session は停止されました(回答は途中まで)。'
		});
	});

	it('subagent 宣言: usage の宣言外の形は捨てる(型に忠実)', () => {
		const content = JSON.stringify({
			ok: true,
			session_id: 'child-chat-3',
			answer: 'x',
			usage: { prompt_tokens: '多い', total_tokens: 99 }
		});
		expect(classifyToolResult(content, { kind: 'subagent' })).toEqual({
			kind: 'subagent',
			sessionId: 'child-chat-3',
			ok: true,
			answer: 'x'
		});
	});

	it('execution: spawn 即返し形 {ok, exec_id, tool_name, status: "running"} は descriptor より先に shape で受ける(#110)', () => {
		// spawn call の descriptor は実 tool(subagent 等)のもの — subagent 宣言が
		// 付いていても exec running 形は execution として描く
		const content = JSON.stringify({
			ok: true,
			exec_id: 'exec-1111',
			tool_name: 'run_subagent',
			status: 'running'
		});
		expect(classifyToolResult(content, { kind: 'subagent' })).toEqual({
			kind: 'execution',
			execId: 'exec-1111',
			toolName: 'run_subagent'
		});
		expect(classifyToolResult(content)).toEqual({
			kind: 'execution',
			execId: 'exec-1111',
			toolName: 'run_subagent'
		});
	});

	it('execution: tool_name 欠落(await_async timeout 形 {exec_id, status, elapsed_s})も受ける(#110)', () => {
		const content = JSON.stringify({ exec_id: 'exec-2222', status: 'running', elapsed_s: 12 });
		expect(classifyToolResult(content)).toEqual({
			kind: 'execution',
			execId: 'exec-2222'
		});
	});

	it('execution: 封筒(await_async の done 回収形)を剥がして中身を descriptor で描く(#110)', () => {
		// BE は results[].render を実 tool の宣言(exec 行の render_descriptor)で
		// 上書きしてくる。descriptor が指すのは封筒ではなく **result の中身**
		const inner = {
			ok: true,
			session_id: 'child-chat-9',
			answer: '子エージェントの回答',
			usage: { prompt_tokens: 10, completion_tokens: 20 }
		};
		const envelope = JSON.stringify({
			ok: true,
			exec_id: 'exec-5555',
			tool_name: 'run_subagent',
			status: 'done',
			result: inner, // BE が json.loads 済みの生 object で載せる(二重エスケープ回避)
			duration: 12.5,
			usage: { prompt_tokens: 10 }
		});
		expect(classifyToolResult(envelope, { kind: 'subagent' })).toEqual({
			kind: 'subagent',
			sessionId: 'child-chat-9',
			ok: true,
			answer: '子エージェントの回答',
			usage: { prompt_tokens: 10, completion_tokens: 20 }
		});
	});

	it('execution: 封筒剥がしは中身が文字列でも効く(text / markdown / code 宣言)', () => {
		const wrap = (result: unknown) =>
			JSON.stringify({
				ok: true,
				exec_id: 'exec-6666',
				tool_name: 'probe',
				status: 'done',
				result
			});

		expect(classifyToolResult(wrap('本文だけの結果'), { kind: 'text' })).toEqual({
			kind: 'text',
			value: '本文だけの結果'
		});
		expect(classifyToolResult(wrap('# 見出し'), { kind: 'markdown' })).toEqual({
			kind: 'markdown',
			value: '# 見出し'
		});
		expect(classifyToolResult(wrap('print(1)'), { kind: 'code', language: 'python' })).toEqual({
			kind: 'code',
			language: 'python',
			value: 'print(1)'
		});
		// 中身が dict の tool + 宣言なし: 封筒由来の kind=json は中身に効かせてよい
		expect(classifyToolResult(wrap({ a: 1 }), { kind: 'json' })).toEqual({
			kind: 'json',
			value: JSON.stringify({ a: 1 }, null, 2)
		});
		// 中身が**文字列**での kind=json は封筒 dict 由来(または切詰めの降格)—
		// 実 tool が json を宣言していれば BE は object を返す。text に落とす
		expect(classifyToolResult(wrap('ただの文字列'), { kind: 'json' })).toEqual({
			kind: 'text',
			value: 'ただの文字列'
		});
	});

	it('execution: 結果本文の無い終端形(failed/stopped/orphaned)は execution 表示のまま(#110)', () => {
		const failed = JSON.stringify({
			ok: false,
			exec_id: 'exec-7777',
			tool_name: 'run_subagent',
			status: 'failed',
			error: '壊れた'
		});
		expect(classifyToolResult(failed, { kind: 'subagent' })).toEqual({
			kind: 'execution',
			execId: 'exec-7777',
			toolName: 'run_subagent',
			status: 'failed',
			error: '壊れた'
		});
		// check_async の terminal 形(結果本文を返さない)も同じ状態表示に落ちる
		const checked = JSON.stringify({
			exec_id: 'exec-8888',
			tool_name: 'probe',
			status: 'stopped',
			elapsed_s: 3,
			hint: '結果は await_async(exec_id) で回収してください。'
		});
		expect(classifyToolResult(checked)).toEqual({
			kind: 'execution',
			execId: 'exec-8888',
			toolName: 'probe',
			status: 'stopped'
		});
	});

	it('execution: 封筒に見えない形は通常分岐へ(#110)', () => {
		// 未知 status は封筒扱いしない(形が合わなければ descriptor どおり)
		const unknown = JSON.stringify({ exec_id: 'exec-9999', status: 'queued' });
		expect(classifyToolResult(unknown, { kind: 'json' })).toEqual({
			kind: 'json',
			value: JSON.stringify({ exec_id: 'exec-9999', status: 'queued' }, null, 2)
		});
		// 'exec_id' を本文に含むだけの非 JSON も text のまま
		expect(classifyToolResult('exec_id exec-4444 is running')).toEqual({
			kind: 'text',
			value: 'exec_id exec-4444 is running'
		});
	});

	it('subagent 宣言: answer 無しで status も running でない形は json に落とす(型に忠実)', () => {
		const content = JSON.stringify({ ok: true, session_id: 'child-chat-5', status: 'queued' });
		expect(classifyToolResult(content, { kind: 'subagent' })).toEqual({
			kind: 'json',
			value: JSON.stringify({ ok: true, session_id: 'child-chat-5', status: 'queued' }, null, 2)
		});
	});

	it('subagent 宣言: answer 付き(完走形)に status が紛れても answer 形として扱う', () => {
		// BE の完走形は status を同梱しない(#109)。仮に紛れても running 扱いにしない —
		// answer の有無が権威
		const content = JSON.stringify({
			ok: true,
			session_id: 'child-chat-6',
			answer: '完了しました。',
			status: 'running'
		});
		expect(classifyToolResult(content, { kind: 'subagent' })).toEqual({
			kind: 'subagent',
			sessionId: 'child-chat-6',
			ok: true,
			answer: '完了しました。'
		});
	});

	it('subagent 宣言: session_id 不在(spawn 前バリデーションエラー)は json に落とす', () => {
		// nucllei/utils/subagent.py の _error は {ok: false, error} だけを返す
		const content = JSON.stringify({ ok: false, error: 'instruction は必須です。' });
		expect(classifyToolResult(content, { kind: 'subagent' })).toEqual({
			kind: 'json',
			value: JSON.stringify({ ok: false, error: 'instruction は必須です。' }, null, 2)
		});
	});

	it('subagent 宣言でも JSON でない実値は text に落とす(壊れた表示を作らない)', () => {
		expect(classifyToolResult('boom', { kind: 'subagent' })).toEqual({
			kind: 'text',
			value: 'boom'
		});
	});

	it('skill 宣言でも形が合わない実値は text に落とす(壊れた表示を作らない・解釈しない)', () => {
		expect(classifyToolResult('{"message": "boom"}', { kind: 'skill' }).kind).toBe('text');
		expect(
			classifyToolResult(JSON.stringify({ result: 'x', tools: 'nope' }), { kind: 'skill' }).kind
		).toBe('text');
		expect(classifyToolResult('skill not found: nope', { kind: 'skill' })).toEqual({
			kind: 'text',
			value: 'skill not found: nope'
		});
	});
});

describe('formatJSONString', () => {
	it('二重に文字列化された JSON を剥がして整形する(旧実装互換)', () => {
		expect(formatJSONString(JSON.stringify(JSON.stringify({ a: 1 })))).toBe(
			JSON.stringify({ a: 1 }, null, 2)
		);
	});

	it('JSON でない文字列はそのまま引用表現に(引数の一枚岩フォールバック用)', () => {
		expect(formatJSONString('晴れ')).toBe('"晴れ"');
	});

	it('スカラー JSON でも無限再帰しない(旧実装はスタックオーバーフロー頼みだった)', () => {
		expect(formatJSONString('42')).toBe('"42"');
		expect(formatJSONString('null')).toBe('"null"');
	});
});
