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
