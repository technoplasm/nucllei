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

// chatId → handler の Set。同一 chat_id に複数の購読者が居る(同一子 session を指す
// run_subagent ブロックが複数展開される・観測 view とインラインブロックの併存等)ため
// 単一スロットにしない — 後勝ち上書きは先行購読者を無通知で凍らせる。
const chatHandlers = new Map<string, Set<EnvelopeHandler>>();
/** 表示中でないチャットのイベント既定処理(+layout が一度だけ設定する) */
let fallbackHandler: EnvelopeHandler | null = null;

export function registerChatHandler(chatId: string, handler: EnvelopeHandler) {
	let handlers = chatHandlers.get(chatId);
	if (!handlers) {
		handlers = new Set();
		chatHandlers.set(chatId, handlers);
	}
	handlers.add(handler);
}

/** 参照一致で解除する — 同一 chat_id の他の購読者は消さない */
export function unregisterChatHandler(chatId: string, handler: EnvelopeHandler) {
	const handlers = chatHandlers.get(chatId);
	if (!handlers) {
		return;
	}
	handlers.delete(handler);
	if (handlers.size === 0) {
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

/**
 * llens(#110): poll tick(Chat の attachNewChatTasks)のアタッチ判定を一点に集約 —
 * サーバが返した task 一覧から、いまアタッチしてよい id だけを返す。
 *
 * - 走行中 context が既に持つ id / attach 済み(fetch 未解決含む)id は除外
 * - この chat に taskId 未確定(task イベント未着)の走行中 context が居る間は
 *   全件スキップ([] を返す)。送信直後の自タブのターンは task_id が届くまで
 *   taskId: null のまま activeStreams に居て getActiveTaskIds に現れない — この窓で
 *   サーバ一覧の id へアタッチすると同じ task に二重アタッチし delta が二重適用される。
 *   確定後の tick では通常の除外ガードが効く(スキップは 1 tick 遅延するだけ)。
 */
export function selectAttachableTaskIds(
	chatId: string,
	serverTaskIds: string[],
	attachedTaskIds: ReadonlySet<string>
): string[] {
	const contexts = [...(activeStreams.get(chatId) ?? [])];
	if (contexts.some((context) => context.taskId === null)) {
		return [];
	}
	const activeTaskIds = new Set(
		contexts.map((context) => context.taskId).filter((taskId): taskId is string => taskId !== null)
	);
	return serverTaskIds.filter(
		(taskId) => !activeTaskIds.has(taskId) && !attachedTaskIds.has(taskId)
	);
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

				const handlers = chatHandlers.get(chatId);
				if (handlers && handlers.size > 0) {
					// 登録済みの全 handler へ順に配る。1 つの handler の例外(同期 throw /
					// 非同期 reject)は他の handler・ストリーム読取りへ波及させない。
					// 配送中の解除(done → 自 handler を unregister)に備えスナップショットで回す。
					//
					// 同期で適用できたイベント(戻り値なし)は await しない — await を
					// 挟むと 1 イベントごとに microtask 境界(= Svelte の flush)が入り、
					// BE が速いときの適用がレンダリング律速で滞留する。1 回の read で
					// 届いた分は同期一括適用 = flush 1 回に自然にまとまる(BE が FE より
					// 速いほどチャンクに多くのイベントが乗る自己均衡)。非同期の副作用を
					// 持つイベント(done / chat:title 等)だけ Promise が返り、順序を
					// 保って待つ。
					for (const handler of [...handlers]) {
						try {
							const result = handler(envelope, context);
							if (result) {
								await result;
							}
						} catch (error) {
							console.error('chat handler failed', chatId, error);
						}
					}
				} else if (fallbackHandler) {
					const result = fallbackHandler(envelope, context);
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
