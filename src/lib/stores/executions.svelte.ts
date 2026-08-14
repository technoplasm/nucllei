/**
 * 表示中 chat の execution 行(#110)の FE キャッシュ。
 *
 * データ源は exec 行一覧 API(GET /chats/{id}/executions — 5 秒ポーリング。SSE push は
 * しない仕様)。読み手はバックグラウンドチップ(MessageInput/BackgroundTasksChip)と
 * ExecutionBlock(spawn 即返し結果の現在状態表示)。
 *
 * **refreshAndScanExecutions() に一本化**: fetch → store 差し替え → terminal 未回収の
 * enqueue 走査(execution-resume store へ)を**常に**実行する。loadChat seed / onTurnEnd /
 * poll tick / 個別停止後の全経路がこれを呼ぶ — tick の述語ゲート内にだけ走査を置くと
 * リロード後・turn 末完了の自動再開が永久に回らない(仕様 §9 の fe 批評修正)。
 * poll 述語(Chat.svelte)は cadence 制御のみ。
 *
 * state は chatId でスコープし(#107 W-A の規律)、読み手は自分の chatId と一致する
 * ときだけ entries を見る — chat 切替で前 chat の残骸が見えない。
 */

import { getChatExecutions, type ChatExecutionItem } from '$lib/apis/chats';
import type { ChatHistory } from '$lib/types/chat';
import {
	enqueueExecutionResume,
	isExecutionResumeHandled,
	type ExecutionResumeStatus
} from './execution-resume.svelte';

export type ExecutionEntry = ChatExecutionItem;

const TERMINAL_STATUSES = new Set(['done', 'failed', 'stopped', 'orphaned']);

/** 終端状態か(running 以外の既知状態)。未知の文字列は終端扱いしない(誤 enqueue 防止)。 */
const isTerminalExecutionStatus = (status: string): boolean => TERMINAL_STATUSES.has(status);

const state = $state<{
	chatId: string | null;
	entries: ExecutionEntry[];
	/** 直近の走査時点で terminal ∧ 履歴未処理だった exec_id(チップの件数バッジ用)。 */
	unrecoveredIds: string[];
	/** 直近 refresh の失敗(ネットワーク断等)。poll 述語の自己接地 — 失敗中は poll を止めない。 */
	lastRefreshFailed: boolean;
}>({ chatId: null, entries: [], unrecoveredIds: [], lastRefreshFailed: false });

/** 遅延解決した古い fetch が新しい状態を上書きしないためのシーケンス。 */
let requestSeq = 0;

/** chat 切替・新規チャットでの全消し(前 chat の残骸を持ち越さない)。 */
export const clearExecutions = () => {
	requestSeq += 1;
	state.chatId = null;
	state.entries = [];
	state.unrecoveredIds = [];
	state.lastRefreshFailed = false;
};

/**
 * exec 行一覧を取得して差し替え、terminal 未回収の enqueue 走査を**常に**行う。
 * fetch 失敗時は表示を現状維持(チップを瞬断でちらつかせない)しつつ
 * lastRefreshFailed を立て、保持中の entries で走査だけは実行する。
 * enqueue の重複は execution-resume 側の exec 単位 once ガードが防ぐ。
 */
export const refreshAndScanExecutions = async (chatId: string, history: ChatHistory) => {
	if (!chatId) {
		return;
	}
	const seq = ++requestSeq;
	const executions = await getChatExecutions(chatId).catch(() => null);
	if (seq !== requestSeq) {
		return;
	}
	if (executions === null) {
		if (state.chatId !== chatId) {
			// 前 chat の entries で走査しない — この chat の状態は「不明・空」として扱う
			state.chatId = chatId;
			state.entries = [];
			state.unrecoveredIds = [];
		}
		state.lastRefreshFailed = true;
	} else {
		state.chatId = chatId;
		state.entries = executions;
		state.lastRefreshFailed = false;
	}

	// 走査(常に実行): terminal ∧ 履歴未処理 → resume 要求 + チップ件数
	const unrecovered: string[] = [];
	for (const entry of state.entries) {
		if (!isTerminalExecutionStatus(entry.status)) {
			continue;
		}
		if (isExecutionResumeHandled(history, entry.id)) {
			continue;
		}
		unrecovered.push(entry.id);
		enqueueExecutionResume(chatId, {
			execId: entry.id,
			toolName: entry.tool_name,
			status: entry.status as ExecutionResumeStatus
		});
	}
	state.unrecoveredIds = unrecovered;
};

/** exec_id で 1 行引く(ExecutionBlock / ToolCallBlock のバッジ上書き)。 */
export const getExecutionEntry = (execId: string): ExecutionEntry | null =>
	state.entries.find((entry) => entry.id === execId) ?? null;

/** 読み手用のリアクティブな参照。プロパティ読み取りで購読される。 */
export const executionsState = state;
