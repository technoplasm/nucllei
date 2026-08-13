/**
 * 表示中 chat の「実行中 background subagent セッション」一覧(#110)。
 *
 * 入力欄下部行の「バックグラウンド」チップ(MessageInput/BackgroundTasksChip)が読む
 * 揮発 state。書き手は Chat.svelte の background spawn 常時検知ポーリング(5 秒 tick で
 * 走行中 session の全量を setBackgroundExecs で差し替える)と、SubagentSessionBlock の
 * live done 検知(removeBackgroundExec で即時に落とす — 次 tick を待たない)。
 *
 * resume の enqueue 済み(requested)かどうかは表示に関与しない — 検知ゲート
 * (subagent-resume 側)と表示は別物で、task が生きている限りチップに出す。
 * state は chatId でスコープし(#107 W-A の規律)、読み手は自分の chatId と一致する
 * ときだけ entries を見る — chat 切替で前 chat の残骸が見えない。
 */

export type BackgroundExec = {
	sessionId: string;
	/** このセッションを走行中として最初に観測した時刻(クライアント時計)。表示順の安定用。 */
	firstSeenAt: number;
};

const state = $state<{ chatId: string | null; entries: BackgroundExec[] }>({
	chatId: null,
	entries: []
});

/**
 * ポーリング tick の全量差し替え。既存 entry の firstSeenAt と順序は保持し、
 * 新規は末尾に追う(tick ごとにチップ内の並びが揺れない)。
 */
export const setBackgroundExecs = (chatId: string, sessionIds: string[]) => {
	if (!chatId) {
		return;
	}
	const now = Date.now();
	const previous = state.chatId === chatId ? state.entries : [];
	const wanted = new Set(sessionIds);
	const kept = previous.filter((entry) => wanted.has(entry.sessionId));
	const known = new Set(kept.map((entry) => entry.sessionId));
	for (const sessionId of sessionIds) {
		if (!known.has(sessionId)) {
			kept.push({ sessionId, firstSeenAt: now });
		}
	}
	state.chatId = chatId;
	state.entries = kept;
};

/** live done 検知(SubagentSessionBlock)からの即時削除。未登録なら何もしない。 */
export const removeBackgroundExec = (sessionId: string) => {
	if (!sessionId) {
		return;
	}
	const index = state.entries.findIndex((entry) => entry.sessionId === sessionId);
	if (index !== -1) {
		state.entries.splice(index, 1);
	}
};

/** chat 切替・新規チャットでの全消し(前 chat の残骸を持ち越さない)。 */
export const clearBackgroundExecs = () => {
	state.chatId = null;
	state.entries = [];
};

/** 表示中 chat の実行中一覧(chatId 不一致・未設定は空)。 */
export const getBackgroundExecs = (chatId: string | null | undefined): BackgroundExec[] =>
	chatId && state.chatId === chatId ? state.entries : [];

/** 読み手用のリアクティブな参照。プロパティ読み取りで購読される。 */
export const backgroundExecsState = state;
