// tool 結果の表(kind="table")のテスト。仕様: _memos/tool-arg-render.md「table の再導入」。

import { describe, expect, it } from 'vitest';

import { formatTableMeta, parseCsv, parseCsvTable, TABLE_MAX_ROWS } from './csv';
import { classifyToolResult } from './index';

describe('parseCsv', () => {
	it('素の CSV を行 × セルに読む(末尾の改行は空レコードにしない)', () => {
		expect(parseCsv('a,b\n1,2\n')).toEqual([
			['a', 'b'],
			['1', '2']
		]);
		expect(parseCsv('a,b\r\n1,2')).toEqual([
			['a', 'b'],
			['1', '2']
		]);
	});

	it('引用符: カンマ・改行(LF / CRLF)を含められ、"" は " 1 個', () => {
		expect(parseCsv('name,note\n"Doe, J","line1\nline2"\n"x","say ""hi"""\n')).toEqual([
			['name', 'note'],
			['Doe, J', 'line1\nline2'],
			['x', 'say "hi"']
		]);
		expect(parseCsv('a\r\n"p\r\nq"\r\n')).toEqual([['a'], ['p\r\nq']]);
	});

	it('空セル(素・引用符とも)と空行', () => {
		expect(parseCsv('a,b,c\n,,\n"",x,\n')).toEqual([
			['a', 'b', 'c'],
			['', '', ''],
			['', 'x', '']
		]);
		// 引用符で囲んだ空で終わる(末尾改行なし)
		expect(parseCsv('a\n""')).toEqual([['a'], ['']]);
		// 途中の空行は空セル 1 個の行
		expect(parseCsv('a\n\nb\n')).toEqual([['a'], [''], ['b']]);
	});

	it('壊れた引用符は null', () => {
		// 閉じない
		expect(parseCsv('a,b\n"open,1\n')).toBeNull();
		// 閉じ引用符の後に文字
		expect(parseCsv('a,b\n"x"y,1\n')).toBeNull();
		// 引用符で始まらないフィールドの途中の "
		expect(parseCsv('a,b\nx"y,1\n')).toBeNull();
	});

	it('空文字は 0 行', () => {
		expect(parseCsv('')).toEqual([]);
	});
});

describe('parseCsvTable', () => {
	it('meta 行があるときだけ先頭 1 行を飛ばす(中身の # で推測しない)', () => {
		const text = '# rows=2 truncated=false duration=0.1s\nid,name\n1,a\n2,b\n';
		expect(parseCsvTable(text, true)).toEqual({
			columns: ['id', 'name'],
			rows: [
				['1', 'a'],
				['2', 'b']
			],
			totalRows: 2
		});
		// meta が無ければ # 行も CSV の見出しとして読む(推測しない)
		expect(parseCsvTable('# meta\nid\n1\n')?.columns).toEqual(['# meta']);
		// CRLF の meta 行
		expect(parseCsvTable('# rows=1\r\nh\r\nv\r\n', true)).toMatchObject({
			columns: ['h'],
			rows: [['v']]
		});
	});

	it('列数の揃わない行は許容して最大列数に揃える', () => {
		expect(parseCsvTable('a,b\n1\n1,2,3\n')).toEqual({
			columns: ['a', 'b', ''],
			rows: [
				['1', '', ''],
				['1', '2', '3']
			],
			totalRows: 2
		});
	});

	it('見出しだけの CSV は 0 行の表', () => {
		expect(parseCsvTable('a,b\n')).toEqual({ columns: ['a', 'b'], rows: [], totalRows: 0 });
	});

	it('表として出せないものは null(壊れた CSV・meta 行だけ・空)', () => {
		expect(parseCsvTable('a\n"x\n')).toBeNull();
		expect(parseCsvTable('# rows=0', true)).toBeNull();
		expect(parseCsvTable('# rows=0\n', true)).toBeNull();
		expect(parseCsvTable('')).toBeNull();
	});

	it('行上限: 先頭 maxRows 行だけ描き、総数は totalRows に残す', () => {
		const body = Array.from({ length: 1500 }, (_, k) => `${k}`).join('\n');
		const table = parseCsvTable(`n\n${body}\n`);
		expect(table?.rows).toHaveLength(TABLE_MAX_ROWS);
		expect(table?.rows[TABLE_MAX_ROWS - 1]).toEqual([`${TABLE_MAX_ROWS - 1}`]);
		expect(table?.totalRows).toBe(1500);
		expect(parseCsvTable('n\n1\n2\n3\n', false, 2)).toMatchObject({
			rows: [['1'], ['2']],
			totalRows: 3
		});
	});
});

describe('formatTableMeta', () => {
	it('スカラーを「キー: 値」用の文字列に(bool は true / false、null は空)', () => {
		expect(
			formatTableMeta({ rows: 120, truncated: false, ok: true, duration: '0.3s', none: null })
		).toEqual([
			['rows', '120'],
			['truncated', 'false'],
			['ok', 'true'],
			['duration', '0.3s'],
			['none', '']
		]);
	});

	it('object 以外・非スカラーの値は捨てる', () => {
		expect(formatTableMeta(null)).toEqual([]);
		expect(formatTableMeta([1])).toEqual([]);
		expect(formatTableMeta({ a: [1], b: { c: 1 }, d: 1 })).toEqual([['d', '1']]);
	});
});

describe('classifyToolResult — table', () => {
	it('descriptor が table のときだけ表になる。meta は descriptor から、本文の meta 行は飛ばす', () => {
		const display = classifyToolResult('# rows=1 truncated=false\na,b\n1,2\n', {
			kind: 'table',
			meta: { rows: 1, truncated: false }
		});
		expect(display).toEqual({
			kind: 'table',
			meta: [
				['rows', '1'],
				['truncated', 'false']
			],
			columns: ['a', 'b'],
			rows: [['1', '2']],
			totalRows: 1
		});
		// meta が無ければ本文はまるごと CSV
		expect(classifyToolResult('a\n1\n', { kind: 'table' })).toEqual({
			kind: 'table',
			meta: [],
			columns: ['a'],
			rows: [['1']],
			totalRows: 1
		});
		// 空の meta も meta 無し(BE は空の meta を descriptor に載せないが、来ても行を飛ばさない)
		expect(classifyToolResult('a\n1\n', { kind: 'table', meta: {} })).toMatchObject({
			kind: 'table',
			columns: ['a']
		});
	});

	it('壊れた CSV は text に戻す', () => {
		expect(classifyToolResult('a\n"x\n', { kind: 'table' })).toEqual({
			kind: 'text',
			value: 'a\n"x\n'
		});
		expect(classifyToolResult('# rows=0', { kind: 'table', meta: { rows: 0 } })).toEqual({
			kind: 'text',
			value: '# rows=0'
		});
	});

	it('descriptor が無ければ CSV らしくても text(推測しない)', () => {
		expect(classifyToolResult('a,b\n1,2\n')).toEqual({ kind: 'text', value: 'a,b\n1,2\n' });
	});
});
