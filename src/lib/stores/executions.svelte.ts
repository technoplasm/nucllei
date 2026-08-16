/**
 * 表示中 chat の execution 行(#110)の FE キャッシュ。
 *
 * データ源は exec 行一覧 API(GET /chats/{id}/executions — 5 秒ポーリング。SSE push は
 * しない仕様)。読み手はバックグラウンドチップ(MessageInput/BackgroundTasksChip)と
 * ExecutionBlock(spawn 即返し結果の現在状態表示)と poll 述語(Chat.svelte)。
 *
 * 自動再開はサーバ駆動(契約 T3)— FE は履歴を推測して回収要求を積まない。
 * 未回収の判定は API の delivered フィールド由来(terminal ∧ !delivered)で、
 * 配達(通知ターン)・回収 tool(await_task / stop_task)が BE 側で立てる。
 *
 * state は chatId でスコープし(#107 W-A の規律)、読み手は自分の chatId と一致する
 * ときだけ entries を見る — chat 切替で前 chat の残骸が見えない。
 */

import { getChatExecutions, type ChatExecutionItem } from '$lib/apis/chats';

export type ExecutionEntry = ChatExecutionItem;

const TERMINAL_STATUSES = new Set(['done', 'failed', 'stopped', 'orphaned']);

/** 終端状態か(running 以外の既知状態)。未知の文字列は終端扱いしない(誤検知防止)。 */
export const isTerminalExecutionStatus = (status: string): boolean => TERMINAL_STATUSES.has(status);

/** 未配達 terminal(チップの未回収バッジ・poll 継続の根拠)。 */
export const isUndeliveredTerminal = (entry: ExecutionEntry): boolean =>
	isTerminalExecutionStatus(entry.status) && !entry.delivered;

/** 配達直後の追従窓(秒)。BE 発の続きターン発見(attachToTask)を取りこぼさない。 */
export const RECENT_DELIVERY_WINDOW_S = 10 * 60;

/** poll の基本間隔(Chat.svelte のタイマー)と、連続失敗時 backoff の上限(B-2)。 */
export const EXECUTION_POLL_MS = 5000;
export const EXECUTION_POLL_BACKOFF_MAX_MS = 60_000;

const state = $state<{
	chatId: string | null;
	entries: ExecutionEntry[];
	/** 連鎖上限で BE の配達(自動再開)が保留中(一覧 API の top-level。チップ表示)。 */
	resumeSuspended: boolean;
	/** 直近 refresh の失敗(ネットワーク断等)。poll 述語の自己接地 — 失敗中は poll を止めない。 */
	lastRefreshFailed: boolean;
}>({ chatId: null, entries: [], resumeSuspended: false, lastRefreshFailed: false });

/** 遅延解決した古い fetch が新しい状態を上書きしないためのシーケンス。 */
let requestSeq = 0;

/** 同一 chat 内での delivered 増分検知の基準(chat 切替・clear でリセット)。 */
let deliveredBaseline: Set<string> | null = null;

/** B-2: 連続失敗回数と、次の poll 試行を許す時刻(ms)。成功・clear でリセット。 */
let consecutiveRefreshFailures = 0;
let refreshBackoffUntilMs = 0;

/** chat 切替・新規チャットでの全消し(前 chat の残骸を持ち越さない)。 */
export const clearExecutions = () => {
	requestSeq += 1;
	state.chatId = null;
	state.entries = [];
	state.resumeSuspended = false;
	state.lastRefreshFailed = false;
	deliveredBaseline = null;
	consecutiveRefreshFailures = 0;
	refreshBackoffUntilMs = 0;
};

/**
 * exec 行一覧を取得して差し替える。fetch 失敗時は表示を現状維持(チップを瞬断で
 * ちらつかせない)しつつ lastRefreshFailed を立てる(poll 述語の自己接地)。
 *
 * 返り値 deliveredAdvanced: **同一 chat の前回観測と比べて** delivered な行が
 * 増えたか。非表示タブ休止中(poll 停止)にサーバ駆動配達が完了すると、復帰後の
 * tick では続きターンが既に終わっていて attach 経路(未知 message_id 封筒 →
 * refetch)が発火しない — この増分を合図に呼び出し側(Chat.svelte)が history を
 * 読み直す。chat 切替直後の初回観測は false(fetch 済み history が配達済み分を
 * 含んでいる — 追いつき不要)。
 */
export const refreshExecutions = async (
	chatId: string
): Promise<{ deliveredAdvanced: boolean }> => {
	if (!chatId) {
		return { deliveredAdvanced: false };
	}
	const seq = ++requestSeq;
	const res = await getChatExecutions(chatId).catch(() => null);
	if (seq !== requestSeq) {
		return { deliveredAdvanced: false };
	}
	if (res === null) {
		if (state.chatId !== chatId) {
			// 前 chat の entries を見せない — この chat の状態は「不明・空」として扱う
			state.chatId = chatId;
			state.entries = [];
			state.resumeSuspended = false;
			deliveredBaseline = null;
		}
		state.lastRefreshFailed = true;
		consecutiveRefreshFailures += 1;
		refreshBackoffUntilMs =
			Date.now() +
			Math.min(
				EXECUTION_POLL_MS * 2 ** (consecutiveRefreshFailures - 1),
				EXECUTION_POLL_BACKOFF_MAX_MS
			);
		return { deliveredAdvanced: false };
	}
	const sameChat = state.chatId === chatId;
	state.chatId = chatId;
	state.entries = res.executions ?? [];
	state.resumeSuspended = res.resume_suspended === true;
	state.lastRefreshFailed = false;
	consecutiveRefreshFailures = 0;
	refreshBackoffUntilMs = 0;

	const delivered = new Set(
		state.entries.filter((entry) => entry.delivered).map((entry) => entry.id)
	);
	const advanced =
		sameChat &&
		deliveredBaseline !== null &&
		[...delivered].some((id) => !deliveredBaseline!.has(id));
	deliveredBaseline = delivered;
	return { deliveredAdvanced: advanced };
};

/**
 * poll の cadence 述語(契約 §M6 / 批評 F-2 裁定):
 * lastRefreshFailed ∨ 自タブ taskIds ∨ running ∨ 未配達 terminal ∨
 * (terminal ∧ delivered ∧ finished_at が直近 10 分)。
 * 最後の窓は BE 発の続きターン(配達後の自動再開)を attachToTask で発見するため。
 * seed(loadChat)と onTurnEnd は述語を通さず必ず打つ — 自己接地はそこが担う。
 */
export const shouldPollExecutions = (
	chatId: string | null,
	hasOwnTaskIds: boolean,
	nowEpochS: number
): boolean =>
	state.lastRefreshFailed ||
	hasOwnTaskIds ||
	(state.chatId === chatId &&
		state.entries.some(
			(entry) =>
				entry.status === 'running' ||
				isUndeliveredTerminal(entry) ||
				(isTerminalExecutionStatus(entry.status) &&
					entry.delivered &&
					entry.finished_at != null &&
					nowEpochS - entry.finished_at < RECENT_DELIVERY_WINDOW_S)
		));

/**
 * B-2: 連続失敗中の poll 抑制(5s → 10s → 20s → 40s → 60s cap。成功で解除)。
 * lastRefreshFailed は poll を「続ける」根拠(自己接地)、こちらは試行を「間引く」制御 —
 * 役割が逆なので述語(shouldPollExecutions)には畳み込まない。
 * seed(loadChat)は tick を通らないので対象外(ユーザー起点の即時試行は妨げない)。
 */
export const isExecutionsRefreshBackedOff = (nowMs: number): boolean =>
	nowMs < refreshBackoffUntilMs;

/** exec_id で 1 行引く(ExecutionBlock / ToolCallBlock のバッジ上書き)。 */
export const getExecutionEntry = (execId: string): ExecutionEntry | null =>
	state.entries.find((entry) => entry.id === execId) ?? null;

/** 読み手用のリアクティブな参照。プロパティ読み取りで購読される。 */
export const executionsState = state;
