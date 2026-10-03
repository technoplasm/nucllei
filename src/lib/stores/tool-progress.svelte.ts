/**
 * 実行中 tool の進捗(#100)。
 *
 * **message オブジェクトの外**に置くモジュール state。message に生やすと
 * `saveChatHandler` が history を丸ごと `updateChatById` へ渡すので DB に載ってしまい、
 * 「進捗は揮発」という規律が破れる(完了後に残るのは所要時間だけ)。
 *
 * 完了の権威は tool の戻り(results[i] の到着)であって progress の `done` 宣言では
 * ない。消費側は結果があれば進捗を無視すること — tool が done を撃たずに return する
 * (普通の)ケースも、timeout / 例外で tool のコードを通らないケースも、それで塞げる。
 *
 * キーは message_id + tool_call_id。tool_call_id はモデルが振る値(`functions.probe:0`)で
 * メッセージをまたぐと衝突しうるため、単独では宛先にならない。
 */

/**
 * run_subagent の子 agent session の所在(#140 UI 追従)。BE が dispatch 時に
 * session_id、子ターン起動直後に task_id を載せる(粘着 — 以後の payload にも載る)。
 * session_id = 子 chat id。task_id は GET /api/tasks/{task_id}/events の宛先。
 */
export type SubagentProgress = { session_id: string; task_id?: string };

/**
 * 子 agent session の進み具合(SubagentSessionBlock が子のイベントから判定し、
 * ToolCallBlock のヘッダ表示・自動展開の畳み戻しに使う)。done = 走行中の子ターンが無い。
 */
export type SubagentPhase = 'started' | 'thinking' | 'answering' | 'done';

export type ToolProgress = {
	/** 属するチャット。chat 単位の一括破棄(clearChatProgress)の宛先(#107 W-A)。 */
	chatId: string;
	description: string;
	/** current / total が揃うときだけ決定的。片方でも欠ければ不定(ぐるぐる)。 */
	current?: number;
	total?: number;
	done: boolean;
	/** 子 agent session の所在(run_subagent のみ)。一度観測したら保持する。 */
	subagent?: SubagentProgress;
	/**
	 * この tool_call を最初に観測した時刻(クライアント時計)。経過時間表示の起点。
	 *
	 * backend が call の開始時に 1 発通知する(`sink.mark_started()`)。ブロックは
	 * 実行前の 1 回の emit で一斉に描かれるので、コンポーネントのマウント時刻を
	 * 起点にすると 2 本目以降が前の tool の待ち時間まで数えてしまう。
	 */
	startedAt: number;
	/**
	 * tool が終わった時刻(終端 `done=true` を観測した時刻)。経過時間はここで止める。
	 *
	 * results はラウンド単位で届く(全 call が終わってから 1 回)ので、これが無いと
	 * 先に終わった tool の時計が後続を待つ間ずっと回り続け、確定時に巻き戻る。
	 * スピナーと完了表示の権威はあくまで results の到着。
	 */
	doneAt?: number;
};

// id に現れない区切り(uuid にも `functions.name:0` にも含まれない)。
// 生の NUL をソースに書くと git がファイルごとバイナリ扱いにするのでエスケープで書く
const SEP = '\u0000';
const entries = $state<Record<string, ToolProgress>>({});

const keyOf = (messageId: string, toolCallId: string) => `${messageId}${SEP}${toolCallId}`;

/** SSE の `tool:progress` を適用する(同じ tool_call_id は上書き — 履歴は溜めない)。 */
export const setToolProgress = (
	chatId: string,
	messageId: string,
	payload: {
		tool_call_id?: string;
		description?: string;
		current?: number;
		total?: number;
		done?: boolean;
		subagent?: SubagentProgress;
	}
) => {
	const toolCallId = payload?.tool_call_id;
	if (!chatId || !messageId || typeof toolCallId !== 'string') {
		return;
	}
	const key = keyOf(messageId, toolCallId);
	const previous = entries[key];
	const done = payload.done ?? false;
	entries[key] = {
		chatId,
		description: payload.description ?? '',
		current: payload.current,
		total: payload.total,
		done,
		// 所在は粘着(BE も粘着だが、欠けた payload で消さない防御)。task_id は後着で埋まる
		subagent:
			typeof payload.subagent?.session_id === 'string'
				? { ...previous?.subagent, ...payload.subagent }
				: previous?.subagent,
		// 起点・終点とも最初の 1 件で確定させる(以降の更新で進み直させない)
		startedAt: previous?.startedAt ?? Date.now(),
		doneAt: done ? (previous?.doneAt ?? Date.now()) : previous?.doneAt
	};
};

export const getToolProgress = (messageId: string, toolCallId: string): ToolProgress | null =>
	entries[keyOf(messageId, toolCallId)] ?? null;

/** メッセージ確定時の後始末。放っておくとセッション中ずっと残る。 */
export const clearMessageProgress = (messageId: string) => {
	if (!messageId) {
		return;
	}
	const prefix = `${messageId}${SEP}`;
	for (const key of Object.keys(entries)) {
		if (key.startsWith(prefix)) {
			delete entries[key];
		}
	}
};

/**
 * chat 単位の一括破棄(#107 W-A)。done が届かず clearMessageProgress を通らなかった
 * エントリ(stop / task 消滅 / メッセージ削除等)がセッション中残り続けるのを、
 * 「走行中 task の無いチャットを読み込んだとき」に掃く。走行中のチャットでは呼ばない —
 * 非表示中に fallback handler が記録した進捗が唯一の復元経路(+layout.svelte)。
 */
export const clearChatProgress = (chatId: string) => {
	if (!chatId) {
		return;
	}
	for (const [key, entry] of Object.entries(entries)) {
		if (entry.chatId === chatId) {
			delete entries[key];
		}
	}
};
