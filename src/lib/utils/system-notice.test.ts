// システム挿入通知(#110)の判定契約: meta.system_notice のみが根拠。
// 文面プレフィクス([task] 等)では判定しない — 偽装・書式変更に脆いため。

import { describe, it, expect } from 'vitest';

import { isSystemNoticeMessage } from './system-notice';
import type { ChatMessage } from '$lib/types/chat';

const base: ChatMessage = {
	id: 'm1',
	parentId: null,
	childrenIds: [],
	role: 'user',
	content: '[task] バックグラウンドタスクの結果通知。'
};

describe('isSystemNoticeMessage', () => {
	it('user ロール ∧ meta.system_notice で真', () => {
		expect(isSystemNoticeMessage({ ...base, meta: { system_notice: 'task' } })).toBe(true);
	});

	it('meta 不在・空文字は偽(content の文面では判定しない)', () => {
		expect(isSystemNoticeMessage(base)).toBe(false);
		expect(isSystemNoticeMessage({ ...base, meta: {} })).toBe(false);
		expect(isSystemNoticeMessage({ ...base, meta: { system_notice: '' } })).toBe(false);
	});

	it('assistant ロールは meta があっても偽', () => {
		expect(
			isSystemNoticeMessage({ ...base, role: 'assistant', meta: { system_notice: 'task' } })
		).toBe(false);
	});

	it('null / undefined は偽', () => {
		expect(isSystemNoticeMessage(null)).toBe(false);
		expect(isSystemNoticeMessage(undefined)).toBe(false);
	});
});
