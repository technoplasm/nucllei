import { describe, it, expect, beforeEach } from 'vitest';
import {
	setBackgroundExecs,
	removeBackgroundExec,
	clearBackgroundExecs,
	getBackgroundExecs
} from './background-execs.svelte';

const C1 = 'c1';
const C2 = 'c2';

// モジュール $state はテスト間で生き残る — 各テストの前にリセットする。
beforeEach(() => {
	clearBackgroundExecs();
});

describe('setBackgroundExecs / getBackgroundExecs', () => {
	it('全量差し替えで、既存 entry の firstSeenAt と順序を保持し新規を末尾に足す', () => {
		setBackgroundExecs(C1, ['s1', 's2']);
		const first = getBackgroundExecs(C1).find((e) => e.sessionId === 's1');

		setBackgroundExecs(C1, ['s3', 's1']);
		const entries = getBackgroundExecs(C1);
		// s2 は落ち、s1 は元の firstSeenAt のまま先頭、s3 が末尾に追加される
		expect(entries.map((e) => e.sessionId)).toEqual(['s1', 's3']);
		expect(entries[0].firstSeenAt).toBe(first?.firstSeenAt);
	});

	it('chatId でスコープする — 別 chat の読み手には空、差し替えで前 chat 分は消える', () => {
		setBackgroundExecs(C1, ['s1']);
		expect(getBackgroundExecs(C2)).toEqual([]);
		expect(getBackgroundExecs('')).toEqual([]);

		// chat 切替(別 chat の tick)は前 chat の entries を引き継がない
		setBackgroundExecs(C2, ['s9']);
		expect(getBackgroundExecs(C1)).toEqual([]);
		expect(getBackgroundExecs(C2).map((e) => e.sessionId)).toEqual(['s9']);
	});
});

describe('removeBackgroundExec / clearBackgroundExecs', () => {
	it('live done の即時削除は該当 session だけ落とし、未登録は無視する', () => {
		setBackgroundExecs(C1, ['s1', 's2']);
		removeBackgroundExec('s1');
		removeBackgroundExec('unknown');
		expect(getBackgroundExecs(C1).map((e) => e.sessionId)).toEqual(['s2']);
	});

	it('clear で全消しになる', () => {
		setBackgroundExecs(C1, ['s1']);
		clearBackgroundExecs();
		expect(getBackgroundExecs(C1)).toEqual([]);
	});
});
