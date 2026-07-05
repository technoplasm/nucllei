// tool 引数/結果の型駆動レンダリング(issue #20)のテスト。
// 仕様: _memos/tool-arg-render.md — 名前・長さで推測しない、フォールバックは型に忠実。

import { describe, expect, it } from 'vitest';

import { classifyToolResult, formatJSONString, parseToolArguments } from './index';

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

describe('classifyToolResult', () => {
	it('宣言なしは従来挙動(JSON なら整形して json 表示)', () => {
		const display = classifyToolResult('{"ok": true}', undefined);
		expect(display).toEqual({ kind: 'json', value: JSON.stringify({ ok: true }, null, 2) });
	});

	it('text 宣言はそのまま(pre-wrap テキスト)', () => {
		const display = classifyToolResult('stdout line 1\nstdout line 2', { kind: 'text' });
		expect(display).toEqual({ kind: 'text', value: 'stdout line 1\nstdout line 2' });
	});

	it('code 宣言は言語付き code 表示', () => {
		const display = classifyToolResult('print(1)', { kind: 'code', language: 'python' });
		expect(display).toEqual({ kind: 'code', language: 'python', value: 'print(1)' });
	});

	it('table 宣言: object 配列は列の和集合で表になる', () => {
		const content = JSON.stringify([
			{ name: '内科', count: 12 },
			{ name: '外科', count: 8, note: '增' }
		]);
		const display = classifyToolResult(content, { kind: 'table' });
		expect(display).toEqual({
			kind: 'table',
			columns: ['name', 'count', 'note'],
			rows: [
				['内科', '12', ''],
				['外科', '8', '增']
			]
		});
	});

	it('table 宣言でも表形でない実値は json に落とす(壊れた表を作らない)', () => {
		expect(classifyToolResult('{"error": "boom"}', { kind: 'table' }).kind).toBe('json');
		expect(classifyToolResult('[]', { kind: 'table' }).kind).toBe('json');
		expect(classifyToolResult('[1, 2, 3]', { kind: 'table' }).kind).toBe('json');
		expect(classifyToolResult('not json', { kind: 'table' }).kind).toBe('json');
	});
});

describe('formatJSONString', () => {
	it('二重に文字列化された JSON を剥がして整形する(旧実装互換)', () => {
		expect(formatJSONString(JSON.stringify(JSON.stringify({ a: 1 })))).toBe(
			JSON.stringify({ a: 1 }, null, 2)
		);
	});

	it('JSON でない文字列はそのまま引用表現に', () => {
		expect(formatJSONString('晴れ')).toBe('"晴れ"');
	});
});
