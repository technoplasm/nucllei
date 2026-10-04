/**
 * done 前に SSE が切れた自ターンへの追いつき(モバイルのタブ背面化・回線の瞬断)。
 *
 * 切断は応答の失敗ではない — タスクはサーバー側で走り続け、最終形はサーバーが
 * message に保存する(middleware の _persist_message_snapshot)。FE は message を
 * 未完(done でない = 生成中表示)のまま置き、表示中に戻ったら再訪(loadChat)と
 * 同じ二択で追いつく:
 * - タスクが生きている → 再アタッチ(スナップショットから収束し、done で通常の
 *   completed → 保存へ流れる)
 * - タスクが終わっている → サーバーの保存済み history を読み直す
 *
 * 判定だけをここに置き(vitest で固定)、I/O は呼び出し側(Chat.svelte)が注入する。
 */

export type DetachedTurn = { chatId: string; messageId: string };

export type DetachedTurnDeps = {
	/** いま表示中の chat id */
	currentChatId: () => string;
	/** 対象 message が done 済みか(再アタッチ経由の done 到着で true になる) */
	isDone: (messageId: string) => boolean;
	/** 走行中タスクへ未アタッチ分をアタッチし、サーバーの task 一覧を返す(取得失敗は null) */
	attachTasks: (chatId: string) => Promise<string[] | null>;
	/** サーバーの保存済み history へ読み直す(タスク終了後) */
	reloadHistory: (chatId: string) => Promise<void>;
};

/**
 * 1 回分の追いつき。戻り値 true = 片付いた(呼び出し側は保留を捨てる)、
 * false = まだ(次の機会 — 復帰・poll tick — に再試行する)。
 */
export async function resyncDetachedTurn(
	turn: DetachedTurn,
	deps: DetachedTurnDeps
): Promise<boolean> {
	// 別チャットへ移った — 戻ってきたときの loadChat が再アタッチ / done 化を担う
	if (deps.currentChatId() !== turn.chatId) {
		return true;
	}
	if (deps.isDone(turn.messageId)) {
		return true;
	}
	const taskIds = await deps.attachTasks(turn.chatId);
	if (taskIds === null || deps.currentChatId() !== turn.chatId) {
		return false;
	}
	if (taskIds.length > 0) {
		// 走行中 — アタッチ済みのストリームの done を待つ(次の機会に done を確かめる)
		return deps.isDone(turn.messageId);
	}
	// タスクは終わっている — 最終形はサーバーの history にある
	await deps.reloadHistory(turn.chatId);
	return true;
}
