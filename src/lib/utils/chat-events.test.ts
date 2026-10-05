import { describe, expect, it } from 'vitest';
import { applyCompletionData, applyMessageMutation } from './chat-events';

/**
 * wire v2 の FE 側適用の契約テスト。
 * BE(consume_model_stream)の emit 規約: 初回スナップショット → 純追記 delta →
 * 構造変化/間引きスナップショット → done スナップショット。
 */

const snapshot = (blocks: any[], content?: string) => ({
	content:
		content ??
		blocks
			.filter((b) => b.type === 'text')
			.map((b) => b.content)
			.join('\n\n'),
	content_blocks: blocks
});

describe('applyCompletionData', () => {
	it('スナップショットで content / content_blocks を丸ごと差し替える', () => {
		const message: any = { content: '', content_blocks: undefined };
		const result = applyCompletionData(message, snapshot([{ type: 'text', content: 'こん' }]));
		expect(message.content).toBe('こん');
		expect(message.content_blocks).toEqual([{ type: 'text', content: 'こん' }]);
		expect(result.contentChanged).toBe(true);
		expect(result.done).toBe(false);
	});

	it('text delta は末尾 text ブロックと content に追記する', () => {
		const message: any = { content: 'こん', content_blocks: [{ type: 'text', content: 'こん' }] };
		applyCompletionData(message, { delta: { block: 'text', content: 'にちは' } });
		expect(message.content).toBe('こんにちは');
		expect(message.content_blocks).toEqual([{ type: 'text', content: 'こんにちは' }]);
	});

	it('reasoning delta は末尾 reasoning ブロックに追記し content は変えない', () => {
		const message: any = {
			content: '',
			content_blocks: [{ type: 'reasoning', content: 'うーん', done: false }]
		};
		const result = applyCompletionData(message, {
			delta: { block: 'reasoning', content: 'そうか' }
		});
		expect(message.content_blocks[0].content).toBe('うーんそうか');
		expect(message.content).toBe('');
		expect(result.contentChanged).toBe(false);
	});

	it('delta の適用先が無ければブロックを新設する(途中参加の耐性)', () => {
		const message: any = { content: '' };
		applyCompletionData(message, { delta: { block: 'text', content: 'x' } });
		expect(message.content_blocks).toEqual([{ type: 'text', content: 'x' }]);

		const message2: any = {
			content: '',
			content_blocks: [{ type: 'tool_calls', calls: [], results: [] }]
		};
		applyCompletionData(message2, { delta: { block: 'text', content: 'y' } });
		expect(message2.content_blocks.at(-1)).toEqual({ type: 'text', content: 'y' });
	});

	it('閉じた reasoning ブロックには追記せず新設する', () => {
		const message: any = {
			content: '',
			content_blocks: [{ type: 'reasoning', content: '済', done: true, duration: 1 }]
		};
		applyCompletionData(message, { delta: { block: 'reasoning', content: '新' } });
		expect(message.content_blocks).toHaveLength(2);
		expect(message.content_blocks[1]).toEqual({ type: 'reasoning', content: '新', done: false });
	});

	it('delta 連続 → スナップショットで収束する(BE 規約の再現)', () => {
		const message: any = { content: '', content_blocks: undefined };
		// 初回スナップショット(空)
		applyCompletionData(message, snapshot([]));
		// 純追記
		applyCompletionData(message, { delta: { block: 'text', content: 'abc' } });
		applyCompletionData(message, { delta: { block: 'text', content: 'def' } });
		// 間引きスナップショット(全量)
		applyCompletionData(message, snapshot([{ type: 'text', content: 'abcdef' }]));
		expect(message.content).toBe('abcdef');
		expect(message.content_blocks).toEqual([{ type: 'text', content: 'abcdef' }]);
	});

	it('done スナップショットで done が立つ', () => {
		const message: any = { content: '', content_blocks: [] };
		const result = applyCompletionData(message, {
			done: true,
			...snapshot([{ type: 'text', content: '完' }]),
			title: 'タイトル'
		});
		expect(message.done).toBe(true);
		expect(result.done).toBe(true);
	});

	it('エラーは {error:{content}} の一形で message.error に載る', () => {
		const message: any = {};
		const result = applyCompletionData(message, { error: { content: '接続が失われました' } });
		expect(message.error).toEqual({ content: '接続が失われました' });
		expect(result.error).toBe(true);
	});

	it('usage を反映する', () => {
		const message: any = {};
		applyCompletionData(message, { usage: { total_tokens: 10 } });
		expect(message.usage).toEqual({ total_tokens: 10 });
	});
});

describe('applyMessageMutation', () => {
	it('status は管轄外(撤去済み — 誰も蓄積しない)', () => {
		const message: any = {};
		expect(applyMessageMutation('status', { description: 'a' }, message)).toBe(false);
		expect(message).toEqual({});
	});

	it('task-cancelled は done + interrupted を立てる(cancel 時の順序契約)', () => {
		const message: any = { done: false };
		expect(applyMessageMutation('task-cancelled', null, message)).toBe(true);
		expect(message.done).toBe(true);
		expect(message.interrupted).toBe(true);
	});

	it('chat:message:delta / message は content に追記する', () => {
		const message: any = { content: 'こん' };
		applyMessageMutation('chat:message:delta', { content: 'にち' }, message);
		applyMessageMutation('message', { content: 'は' }, message);
		expect(message.content).toBe('こんにちは');
	});

	it('chat:message / replace は content を置換する', () => {
		const message: any = { content: 'old' };
		applyMessageMutation('replace', { content: 'new' }, message);
		expect(message.content).toBe('new');
	});

	it('chat:message:files / files は files を差し替える', () => {
		const message: any = {};
		applyMessageMutation('files', { files: [{ id: 'f1' }] }, message);
		expect(message.files).toEqual([{ id: 'f1' }]);
	});

	it('source / citation は管轄外(引用表示は撤去済み — 未知の型として呼び出し側へ)', () => {
		const message: any = {};
		expect(applyMessageMutation('source', { source: { name: 'doc' } }, message)).toBe(false);
		expect(applyMessageMutation('citation', { source: { name: 'doc2' } }, message)).toBe(false);
		expect(applyMessageMutation('source', { type: 'code_execution', id: 'x' }, message)).toBe(
			false
		);
		expect(message).toEqual({});
	});

	it('管轄外イベント(chat:completion / chat:title / notification 等)は false', () => {
		const message: any = {};
		for (const type of ['chat:completion', 'chat:title', 'notification', 'confirmation', 'input']) {
			expect(applyMessageMutation(type, {}, message)).toBe(false);
		}
	});
});
