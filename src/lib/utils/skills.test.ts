// /command 判定ヘルパ(issue #35)のテスト。
// サーバは「先頭行の行頭」だけを発火対象にするため、行頭/先頭行/不一致の
// 各境界を固定する(フロントとサーバで述語がズレると素通し挙動と乖離するため)。

import { describe, expect, it } from 'vitest';

import { findSkillByCommand, matchSkillCommand } from './skills';

describe('matchSkillCommand: 行頭一致', () => {
	it('先頭行の行頭 /command は command を返す(先頭 / なし)', () => {
		expect(matchSkillCommand('/summarize')).toBe('summarize');
		expect(matchSkillCommand('/summarize ここを要約して')).toBe('summarize');
		expect(matchSkillCommand('/foo-bar_123')).toBe('foo-bar_123');
	});

	it('先頭行が /command なら 2 行目以降があっても command を返す', () => {
		expect(matchSkillCommand('/translate\nこの文章を訳して')).toBe('translate');
	});
});

describe('matchSkillCommand: 不一致', () => {
	it('空文字・null 相当は null', () => {
		expect(matchSkillCommand('')).toBeNull();
		expect(matchSkillCommand(undefined as unknown as string)).toBeNull();
	});

	it('行頭以外の /foo は対象外', () => {
		expect(matchSkillCommand('please /summarize this')).toBeNull();
		expect(matchSkillCommand(' /summarize')).toBeNull(); // 先頭に空白
	});

	it('2 行目以降の行頭 /foo は対象外(先頭行のみ判定)', () => {
		expect(matchSkillCommand('hello\n/summarize')).toBeNull();
	});

	it('/ 直後に語がない場合は null', () => {
		expect(matchSkillCommand('/')).toBeNull();
		expect(matchSkillCommand('/ space')).toBeNull();
	});
});

describe('findSkillByCommand', () => {
	const skills = [
		{ id: 'summarize', name: 'Summarize' },
		{ id: 'translate', name: 'Translate' }
	];

	it('有効な command はその skill を返す', () => {
		expect(findSkillByCommand(skills, '/summarize これ')).toEqual(skills[0]);
	});

	it('未定義 command は null(サーバ素通しと一致)', () => {
		expect(findSkillByCommand(skills, '/unknown これ')).toBeNull();
	});

	it('skillList が null/未ロードなら null', () => {
		expect(findSkillByCommand(null, '/summarize')).toBeNull();
		expect(findSkillByCommand(undefined, '/summarize')).toBeNull();
	});

	it('行頭でない /command は null', () => {
		expect(findSkillByCommand(skills, 'x /summarize')).toBeNull();
	});
});
