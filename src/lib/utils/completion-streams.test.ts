import { afterEach, describe, expect, it, vi } from 'vitest';

// consumeCompletionStream 自体は fetch を使わない(reply 経路のみ)が、モジュールの
// import チェーン($lib/apis/fetch → $lib/stores → $app/*)を test 環境から切る
vi.mock('$lib/constants', () => ({ WEBUI_BASE_URL: '' }));
vi.mock('$lib/apis/fetch', () => ({ apiFetch: vi.fn() }));

import {
	consumeCompletionStream,
	registerChatHandler,
	unregisterChatHandler,
	setFallbackHandler,
	type CompletionEnvelope,
	type EnvelopeHandler
} from './completion-streams';

/**
 * chatHandlers 多重化(chatId → Set<handler>)の契約テスト。
 *
 * 背景(#107 P3): 同一子 session を指す run_subagent ブロックが複数展開されると
 * 同一 chat_id に複数の購読者が登録される。単一スロット(後勝ち上書き)だと先行
 * ブロックが done を受け取れず「Running...」のまま永久凍結する — 多重配送・
 * 参照一致解除・例外隔離をここで固定する。
 */

const envelope = (chatId: string, seq: number): CompletionEnvelope => ({
	chat_id: chatId,
	message_id: `m${seq}`,
	data: { type: 'chat:completion', data: { seq } }
});

const sseBody = (envelopes: unknown[]): ReadableStream<Uint8Array> =>
	new ReadableStream({
		start(controller) {
			const text = envelopes.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('');
			controller.enqueue(new TextEncoder().encode(text));
			controller.close();
		}
	});

const recorder = () => {
	const received: (string | null)[] = [];
	const handler: EnvelopeHandler = (env) => {
		received.push(env.message_id);
	};
	return { received, handler };
};

afterEach(() => {
	// fallback はモジュール単一スロット — テスト間の漏れを防ぐ
	setFallbackHandler(() => {});
	vi.restoreAllMocks();
});

describe('registerChatHandler / unregisterChatHandler(多重登録)', () => {
	it('同一 chatId に複数登録された handler 全員に配送される', async () => {
		const a = recorder();
		const b = recorder();
		registerChatHandler('c1', a.handler);
		registerChatHandler('c1', b.handler);

		await consumeCompletionStream('c1', sseBody([envelope('c1', 1), envelope('c1', 2)]));

		expect(a.received).toEqual(['m1', 'm2']);
		expect(b.received).toEqual(['m1', 'm2']);

		unregisterChatHandler('c1', a.handler);
		unregisterChatHandler('c1', b.handler);
	});

	it('unregister は参照一致 — 同一 chatId の他の handler は解除しない', async () => {
		const a = recorder();
		const b = recorder();
		registerChatHandler('c2', a.handler);
		registerChatHandler('c2', b.handler);
		unregisterChatHandler('c2', a.handler);

		await consumeCompletionStream('c2', sseBody([envelope('c2', 1)]));

		expect(a.received).toEqual([]);
		expect(b.received).toEqual(['m1']);

		unregisterChatHandler('c2', b.handler);
	});

	it('同一 handler の二重登録は 1 回だけ配送される(Set 意味論)', async () => {
		const a = recorder();
		registerChatHandler('c3', a.handler);
		registerChatHandler('c3', a.handler);

		await consumeCompletionStream('c3', sseBody([envelope('c3', 1)]));

		expect(a.received).toEqual(['m1']);

		unregisterChatHandler('c3', a.handler);
	});
});

describe('配送の例外隔離', () => {
	it('handler の同期 throw は他の handler・後続イベントへ波及しない', async () => {
		const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
		const throwing: EnvelopeHandler = () => {
			throw new Error('boom');
		};
		const b = recorder();
		registerChatHandler('c4', throwing);
		registerChatHandler('c4', b.handler);

		await expect(
			consumeCompletionStream('c4', sseBody([envelope('c4', 1), envelope('c4', 2)]))
		).resolves.toBeUndefined();

		expect(b.received).toEqual(['m1', 'm2']);
		expect(consoleError).toHaveBeenCalled();

		unregisterChatHandler('c4', throwing);
		unregisterChatHandler('c4', b.handler);
	});

	it('handler の非同期 reject も他の handler へ波及しない', async () => {
		const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
		const rejecting: EnvelopeHandler = () => Promise.reject(new Error('async boom'));
		const b = recorder();
		registerChatHandler('c5', rejecting);
		registerChatHandler('c5', b.handler);

		await expect(
			consumeCompletionStream('c5', sseBody([envelope('c5', 1), envelope('c5', 2)]))
		).resolves.toBeUndefined();

		expect(b.received).toEqual(['m1', 'm2']);
		expect(consoleError).toHaveBeenCalled();

		unregisterChatHandler('c5', rejecting);
		unregisterChatHandler('c5', b.handler);
	});
});

describe('fallback handler の発動条件', () => {
	it('未登録の chatId は fallback へ配る', async () => {
		const fallback = recorder();
		setFallbackHandler(fallback.handler);

		await consumeCompletionStream('c6', sseBody([envelope('c6', 1)]));

		expect(fallback.received).toEqual(['m1']);
	});

	it('handler が登録されている間は fallback を呼ばない', async () => {
		const a = recorder();
		const fallback = recorder();
		registerChatHandler('c7', a.handler);
		setFallbackHandler(fallback.handler);

		await consumeCompletionStream('c7', sseBody([envelope('c7', 1)]));

		expect(a.received).toEqual(['m1']);
		expect(fallback.received).toEqual([]);

		unregisterChatHandler('c7', a.handler);
	});

	it('全 handler の解除で Set が空になったら fallback に戻る', async () => {
		const a = recorder();
		const fallback = recorder();
		registerChatHandler('c8', a.handler);
		unregisterChatHandler('c8', a.handler);
		setFallbackHandler(fallback.handler);

		await consumeCompletionStream('c8', sseBody([envelope('c8', 1)]));

		expect(a.received).toEqual([]);
		expect(fallback.received).toEqual(['m1']);
	});
});
