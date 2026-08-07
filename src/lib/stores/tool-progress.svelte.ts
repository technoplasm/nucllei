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

export type ToolProgress = {
	description: string;
	/** current / total が揃うときだけ決定的。片方でも欠ければ不定(ぐるぐる)。 */
	current?: number;
	total?: number;
	done: boolean;
	/**
	 * この tool_call を最初に観測した時刻(クライアント時計)。経過時間表示の起点。
	 *
	 * backend が call の開始時に 1 発通知する(`sink.mark_started()`)。ブロックは
	 * 実行前の 1 回の emit で一斉に描かれるので、コンポーネントのマウント時刻を
	 * 起点にすると 2 本目以降が前の tool の待ち時間まで数えてしまう。
	 */
	startedAt: number;
};

// id に現れない区切り(uuid にも `functions.name:0` にも含まれない)。
// 生の NUL をソースに書くと git がファイルごとバイナリ扱いにするのでエスケープで書く
const SEP = '\u0000';
const entries = $state<Record<string, ToolProgress>>({});

const keyOf = (messageId: string, toolCallId: string) => `${messageId}${SEP}${toolCallId}`;

/** SSE の `tool:progress` を適用する(同じ tool_call_id は上書き — 履歴は溜めない)。 */
export const setToolProgress = (
	messageId: string,
	payload: {
		tool_call_id?: string;
		description?: string;
		current?: number;
		total?: number;
		done?: boolean;
	}
) => {
	const toolCallId = payload?.tool_call_id;
	if (!messageId || typeof toolCallId !== 'string') {
		return;
	}
	const key = keyOf(messageId, toolCallId);
	entries[key] = {
		description: payload.description ?? '',
		current: payload.current,
		total: payload.total,
		done: payload.done ?? false,
		// 起点は最初の 1 件で確定させる(以降の更新で進み直させない)
		startedAt: entries[key]?.startedAt ?? Date.now()
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
