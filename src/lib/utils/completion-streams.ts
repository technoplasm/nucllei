/**
 * completion イベントストリーム(SSE)の消費マネージャ(wire v2)。
 *
 * 所有権: ストリームの読み手は Chat コンポーネントではなくこのモジュール
 * (= ページ遷移で Chat が unmount しても読み続け、完了をフォールバック
 * ハンドラへ届ける)。Chat は自 chat_id のハンドラを registerChatHandler で
 * 登録し、unmount で解除する。
 *
 * 経路:
 * - POST /api/chat/completions の応答 body(SSE)→ consumeCompletionStream
 * - 走行中タスクへの再アタッチ → attachToTask(GET /api/tasks/{id}/events)
 *
 * イベント封筒: {chat_id, message_id, call_id?, data: {type, data}}
 * task イベント({type:'task'})はここで捕捉し onTask へ渡す(stop 用)。
 * __event_call__(call_id 付き)への応答は POST /api/tasks/{task}/calls/{call}。
 */

import { WEBUI_BASE_URL } from '$lib/constants';
import { apiFetch } from '$lib/apis/fetch';

export interface CompletionEnvelope {
	chat_id: string | null;
	message_id: string | null;
	call_id?: string;
	data: { type: string; data?: any };
}

export type EnvelopeHandler = (
	envelope: CompletionEnvelope,
	context: StreamContext
) => void | Promise<void>;

export interface StreamContext {
	chatId: string;
	taskId: string | null;
	/** call_id 付きイベント(confirmation/input)への応答 */
	reply: (callId: string, result: unknown) => Promise<void>;
}

const chatHandlers = new Map<string, EnvelopeHandler>();
/** 表示中でないチャットのイベント既定処理(+layout が一度だけ設定する) */
let fallbackHandler: EnvelopeHandler | null = null;

export function registerChatHandler(chatId: string, handler: EnvelopeHandler) {
	chatHandlers.set(chatId, handler);
}

export function unregisterChatHandler(chatId: string, handler?: EnvelopeHandler) {
	if (!handler || chatHandlers.get(chatId) === handler) {
		chatHandlers.delete(chatId);
	}
}

export function setFallbackHandler(handler: EnvelopeHandler) {
	fallbackHandler = handler;
}

/** chat_id ごとの走行中コンテキスト(再アタッチの重複防止・stop 用 task 参照) */
const activeStreams = new Map<string, Set<StreamContext>>();

export function getActiveTaskIds(chatId: string): string[] {
	return [...(activeStreams.get(chatId) ?? [])]
		.map((context) => context.taskId)
		.filter((taskId): taskId is string => taskId !== null);
}

function trackStream(chatId: string, context: StreamContext) {
	if (!activeStreams.has(chatId)) {
		activeStreams.set(chatId, new Set());
	}
	activeStreams.get(chatId)!.add(context);
}

function untrackStream(chatId: string, context: StreamContext) {
	const set = activeStreams.get(chatId);
	if (set) {
		set.delete(context);
		if (set.size === 0) {
			activeStreams.delete(chatId);
		}
	}
}

async function replyTaskCall(taskId: string, callId: string, result: unknown) {
	await apiFetch(`${WEBUI_BASE_URL}/api/tasks/${taskId}/calls/${callId}`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ result })
	});
}

/**
 * SSE ボディ(POST 応答 or 再アタッチ GET)を EOF まで読み、封筒ごとに
 * 登録ハンドラ(なければフォールバック)へ配る。
 *
 * EOF がストリームの終端(タスク完了)。done イベントで読み終えない —
 * done の後に chat:title 等が続く(wire v2 規約)。
 */
export async function consumeCompletionStream(
	chatId: string,
	body: ReadableStream<Uint8Array>,
	options: { onTask?: (taskId: string) => void } = {}
): Promise<void> {
	const context: StreamContext = {
		chatId,
		taskId: null,
		reply: async (callId, result) => {
			if (context.taskId) {
				await replyTaskCall(context.taskId, callId, result);
			}
		}
	};
	trackStream(chatId, context);

	const reader = body
		.pipeThrough(new TextDecoderStream() as unknown as ReadableWritablePair<string, Uint8Array>)
		.getReader();
	let buffer = '';

	try {
		for (;;) {
			const { value, done } = await reader.read();
			if (done) {
				break;
			}
			buffer += value;

			// SSE のイベント区切りは空行。data: 行のみ使う(コメント・id は来ない)
			let boundary: number;
			while ((boundary = buffer.indexOf('\n\n')) !== -1) {
				const rawEvent = buffer.slice(0, boundary);
				buffer = buffer.slice(boundary + 2);

				const dataLine = rawEvent
					.split('\n')
					.filter((line) => line.startsWith('data:'))
					.map((line) => line.slice(5).trim())
					.join('');
				if (!dataLine) {
					continue;
				}

				let envelope: CompletionEnvelope;
				try {
					envelope = JSON.parse(dataLine);
				} catch {
					continue;
				}

				if (envelope.data?.type === 'task') {
					context.taskId = envelope.data?.data?.task_id ?? null;
					options.onTask?.(context.taskId!);
					continue;
				}

				const handler = chatHandlers.get(chatId) ?? fallbackHandler;
				if (handler) {
					// 同期で適用できたイベント(戻り値なし)は await しない — await を
					// 挟むと 1 イベントごとに microtask 境界(= Svelte の flush)が入り、
					// BE が速いときの適用がレンダリング律速で滞留する。1 回の read で
					// 届いた分は同期一括適用 = flush 1 回に自然にまとまる(BE が FE より
					// 速いほどチャンクに多くのイベントが乗る自己均衡)。非同期の副作用を
					// 持つイベント(done / chat:title 等)だけ Promise が返り、順序を
					// 保って待つ。
					const result = handler(envelope, context);
					if (result) {
						await result;
					}
				}
			}
		}
	} finally {
		untrackStream(chatId, context);
		reader.releaseLock();
	}
}

/**
 * 走行中タスクへ再アタッチする(チャットを開いたとき / リロード後)。
 * サーバーは最新スナップショットを先に流すので、途中からでも収束する。
 */
export async function attachToTask(chatId: string, taskId: string): Promise<void> {
	const res = await apiFetch(`${WEBUI_BASE_URL}/api/tasks/${taskId}/events`, {
		method: 'GET'
	});
	if (!res.ok || !res.body) {
		// タスクが既に終わっている(404)のは正常系 — DB の最終状態が真実
		return;
	}
	await consumeCompletionStream(chatId, res.body);
}
