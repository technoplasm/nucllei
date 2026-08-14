/**
 * execution(#110)の親ターン自動再開の要求キュー。
 *
 * 書き手は executions store の refreshAndScanExecutions(exec 行一覧の fetch 後に
 * terminal 未回収を走査して積む)。読み手は Chat.svelte:「親 chat 表示中 ∧ turn
 * 非走行」のときだけ定型 user メッセージを既存送信経路(send.ts submitPrompt)で
 * **バッチ 1 通**送信し、要求を消費する。親が走行中なら turn 終了時(events.ts の
 * onTurnEnd フック)に再評価される。親画面が閉じている間は再開しない(v1 制約)。
 *
 * 無限ループ防止のガード列(#109 の once/handled/送信ガードを exec_id ベースで再構築):
 * - exec 単位 once(requested 集合、ページセッション内)— 同じ exec の terminal 検知が
 *   何度来ても enqueue は 1 回
 * - 定型メッセージは **full exec_id を本文に必ず含める**(handled 判定の要)。
 *   リロード後の再検知(requested 集合が消える)は isExecutionResumeHandled
 *   (履歴に定型文 or await_async の終端回収結果)で drop する。take 時にも再評価する
 * - 連鎖上限: ユーザー発話なしの自動 turn を chat 単位 EXECUTION_RESUME_CHAIN_LIMIT 回で
 *   停止(countTrailingAutoResumes — 履歴末尾から決定的に数える。チップに表示)
 */

import type { ChatHistory } from '$lib/types/chat';

/** terminal 状態(running 以外)。orphaned も通知対象(結果は失われた旨を報告させる)。 */
export type ExecutionResumeStatus = 'done' | 'failed' | 'stopped' | 'orphaned';

export type ExecutionResumeRequest = {
	chatId: string;
	execId: string;
	toolName: string;
	status: ExecutionResumeStatus;
};

/** ユーザー発話なしの連続自動 turn の上限(chat 単位。仕様 §9 — 既定 5)。 */
export const EXECUTION_RESUME_CHAIN_LIMIT = 5;

const state = $state<{ entries: ExecutionResumeRequest[]; chainLimitedChatId: string | null }>({
	entries: [],
	chainLimitedChatId: null
});

// exec 単位 once ガード(ページセッション内)。take で消費されても再 enqueue しない
const requested = new Set<string>();

/** この exec の resume 要求が enqueue 済みか(走査の再 enqueue 抑止の可視化)。 */
export const isExecutionResumeRequested = (execId: string): boolean => requested.has(execId);

/** terminal 未回収の検知(refreshAndScanExecutions)からの要求。exec 単位で 1 回だけ積む。 */
export const enqueueExecutionResume = (
	chatId: string,
	request: { execId: string; toolName: string; status: ExecutionResumeStatus }
) => {
	if (!chatId || !request.execId || requested.has(request.execId)) {
		return;
	}
	requested.add(request.execId);
	state.entries.push({ chatId, ...request });
};

/** この chat の pending 要求があるか(送信ガードの事前判定 — 消費しない)。 */
export const hasPendingExecutionResume = (chatId: string | null | undefined): boolean =>
	!!chatId && state.entries.some((entry) => entry.chatId === chatId);

/** この chat の要求を**全件**取り出して消費する(バッチ 1 通送信)。他 chat の要求は残す。 */
export const takeExecutionResumes = (chatId: string): ExecutionResumeRequest[] => {
	const taken = state.entries.filter((entry) => entry.chatId === chatId);
	if (taken.length > 0) {
		state.entries = state.entries.filter((entry) => entry.chatId !== chatId);
	}
	return taken;
};

/** 送信失敗時の戻し(once ガードは維持 — 再 enqueue でなく直接戻す)。 */
export const requeueExecutionResumes = (requests: ExecutionResumeRequest[]) => {
	state.entries.push(...requests);
};

/** 連鎖上限で自動再開を停止中の chat(チップ表示用。pending は消費せず残す)。 */
export const setExecutionResumeChainLimited = (chatId: string) => {
	state.chainLimitedChatId = chatId;
};

export const clearExecutionResumeChainLimited = () => {
	state.chainLimitedChatId = null;
};

export const isExecutionResumeChainLimited = (chatId: string | null | undefined): boolean =>
	!!chatId && state.chainLimitedChatId === chatId;

/** 読み手(Chat.svelte の $effect / チップ)用のリアクティブな参照。 */
export const executionResumeState = state;

/** テスト用の全リセット(モジュール $state と once ガードはテスト間で生き残るため)。 */
export const resetExecutionResume = () => {
	state.entries = [];
	state.chainLimitedChatId = null;
	requested.clear();
};

// 自動送信する定型 user メッセージの識別プレフィクス(モデルへの機械指示 — ja 固定)。
// isExecutionResumeMessage / countTrailingAutoResumes / handled 判定が共有する
const RESUME_PREFIX = '[execution] ';

const lineFor = (request: ExecutionResumeRequest): string => {
	switch (request.status) {
		case 'stopped':
			// ユーザー停止も通知する(承認済み Q5 — 報告文で閉じさせる)
			return `- ${request.execId}(${request.toolName}): ユーザーにより停止されました。await_async で最終状態を確認し、状況を報告してください。`;
		case 'orphaned':
			return `- ${request.execId}(${request.toolName}): サーバー再起動により結果は失われました。回収はできません。状況を報告してください。`;
		default:
			// done / failed: await_async で回収し続行(failed も回収形が error を運ぶ)
			return `- ${request.execId}(${request.toolName}): 実行が終了しました。await_async で結果を回収し、続行してください。`;
	}
};

/**
 * バッチ 1 通の定型 user メッセージ。各行に **full exec_id** を必ず含める —
 * handled 判定(下記)とリロード後の drop の要。
 */
export const buildExecutionResumeMessage = (requests: ExecutionResumeRequest[]): string =>
	`${RESUME_PREFIX}バックグラウンド実行が終了しました。以下を処理してください。\n${requests
		.map(lineFor)
		.join('\n')}`;

/** 定型 resume メッセージか(自動 turn の判別 — 連鎖上限のカウント対象)。 */
export const isExecutionResumeMessage = (content: string | null | undefined): boolean =>
	typeof content === 'string' && content.startsWith(RESUME_PREFIX);

/**
 * 履歴末尾からユーザー発話なしの連続自動 turn を数える(連鎖上限の判定)。
 * currentId から parentId 鎖を遡り、user メッセージが定型 resume 文である限り数え、
 * 定型でない user 発話(=人間の介入)で打ち切る。履歴から決定的に導く —
 * 揮発カウンタと違いリロードでリセットされない。
 */
export const countTrailingAutoResumes = (history: ChatHistory): number => {
	let id = history?.currentId ?? null;
	let count = 0;
	const seen = new Set<string>();
	while (id && !seen.has(id)) {
		seen.add(id);
		const message = history.messages[id];
		if (!message) {
			break;
		}
		if (message.role === 'user') {
			if (!isExecutionResumeMessage(message.content)) {
				break;
			}
			count += 1;
		}
		id = message.parentId;
	}
	return count;
};

/**
 * この exec の resume が既に処理済みか(リロード・再走査での再検知を drop する)。
 * 処理済みの根拠:
 * - 定型 resume メッセージ(full exec_id を含む)が履歴の user message にある(自動送信済み)
 * - await_async call(arguments が exec_id を含む)に**終端の**結果が付いている(回収済み)。
 *   timeout の running 形結果({exec_id, status: "running", ...})は未処理扱い —
 *   K5 の「時間切れ → FE 自動再開が後で回収」の経路を塞がない
 */
export const isExecutionResumeHandled = (history: ChatHistory, execId: string): boolean => {
	if (!execId) {
		return true;
	}
	for (const message of Object.values(history?.messages ?? {})) {
		if (
			message.role === 'user' &&
			isExecutionResumeMessage(message.content) &&
			message.content.includes(execId)
		) {
			return true;
		}
		for (const block of message.content_blocks ?? []) {
			if (block.type !== 'tool_calls') {
				continue;
			}
			const calls = (block.calls ?? []) as {
				id?: string;
				function?: { name?: string; arguments?: string };
			}[];
			const results = (block.results ?? []) as { tool_call_id?: string; content?: unknown }[];
			for (const call of calls) {
				if (call?.function?.name !== 'await_async') {
					continue;
				}
				if (!(call?.function?.arguments ?? '').includes(execId)) {
					continue;
				}
				const result = results.find((r) => r?.tool_call_id === call.id);
				if (!result || typeof result.content !== 'string') {
					continue;
				}
				// running 形(timeout)だけを未処理として弾く。それ以外の結果
				// (終端回収・パース不能な壊れ方)は処理済み扱い — 誤って
				// 再送ループに入るより once 側に倒す(一次防衛は定型文チェック)
				try {
					const parsed = JSON.parse(result.content);
					if (parsed !== null && typeof parsed === 'object' && parsed.status === 'running') {
						continue;
					}
				} catch (e) {
					// 非 JSON — 処理済み扱い
				}
				return true;
			}
		}
	}
	return false;
};
