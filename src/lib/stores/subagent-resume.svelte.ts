/**
 * background subagent の親ターン自動再開の要求キュー(#109)。
 *
 * 書き手は SubagentSessionBlock: 「status: running の spawn 即返し結果を表示中に
 * 子の done を検知」したら {parentChatId, sessionId} を積む。読み手は Chat.svelte:
 * 「親 chat 表示中 ∧ turn 非走行」のときだけ定型 user メッセージを既存送信経路
 * (send.ts submitPrompt)で自動送信し、要求を消費する。親が走行中なら turn 終了時
 * (events.ts の onTurnEnd フック)に再評価される。親画面が閉じている間は再開しない
 * (v1 制約 — issue #109)。
 *
 * 無限ループ防止の 3 段ガード:
 * - session 単位 once(このモジュールの requested 集合、ページセッション内)—
 *   同じ子 session の done 検知が何度来ても enqueue は 1 回
 * - 自動送信した turn 自身は新たな要求を生まない — await_subagent の結果は
 *   answer 形(status 無し)なので SubagentSessionBlock の検知対象にならない
 * - リロード後の再検知(requested 集合が消える)は Chat.svelte が
 *   isSubagentResumeHandled(履歴に定型文 or 回収済み結果がある)で drop する
 */

import type { ChatHistory } from '$lib/types/chat';

export type SubagentResumeRequest = {
	parentChatId: string;
	sessionId: string;
};

const state = $state<{ entries: SubagentResumeRequest[] }>({ entries: [] });

// session 単位 once ガード(ページセッション内)。take で消費されても再 enqueue しない
const requested = new Set<string>();

/** この session の resume 要求が enqueue 済みか(常時検知ポーリングの再問い合わせ抑止)。 */
export const isSubagentResumeRequested = (sessionId: string): boolean => requested.has(sessionId);

/** 子の done 検知(SubagentSessionBlock)からの resume 要求。session 単位で 1 回だけ積む。 */
export const requestSubagentResume = (parentChatId: string, sessionId: string) => {
	if (!parentChatId || !sessionId || requested.has(sessionId)) {
		return;
	}
	requested.add(sessionId);
	state.entries.push({ parentChatId, sessionId });
};

/** 親 chat の pending 要求があるか(送信ガードの事前判定 — 消費しない)。 */
export const peekSubagentResume = (parentChatId: string): SubagentResumeRequest | null =>
	state.entries.find((entry) => entry.parentChatId === parentChatId) ?? null;

/** 親 chat の先頭 1 件を取り出して消費する(FIFO)。他 chat の要求は残す。 */
export const takeSubagentResume = (parentChatId: string): SubagentResumeRequest | null => {
	const index = state.entries.findIndex((entry) => entry.parentChatId === parentChatId);
	if (index === -1) {
		return null;
	}
	const [entry] = state.entries.splice(index, 1);
	return entry;
};

/** 読み手(Chat.svelte の $effect)用のリアクティブな参照。entries.length の変化で再評価。 */
export const subagentResumeState = state;

/** テスト用の全リセット(モジュール $state と once ガードはテスト間で生き残るため)。 */
export const resetSubagentResume = () => {
	state.entries = [];
	requested.clear();
};

/** 自動送信する定型 user メッセージ(モデルへの機械指示 — i18n しない・ja 固定)。 */
export const buildSubagentResumeMessage = (sessionId: string): string =>
	`[subagent] session ${sessionId} の実行が完了しました。await_subagent で結果を回収し、続行してください。`;

/**
 * background spawn の即返し形 {ok, session_id, status: "running"}(answer 無し)の
 * subagent tool result を履歴から洗い出し、session_id を重複なしで返す。
 *
 * Chat.svelte の常時検知ポーリング用: SubagentSessionBlock の done 検知は
 * ToolCallBlock を開いているときしか動かないため、「一度も開かれていない
 * ブロック」の background 子はこの走査 + task ポーリングで拾う。
 * 走査はポーリング tick 内でだけ呼ぶこと(turn 中の高頻度評価を避ける)。
 */
export const listRunningSubagentSpawns = (history: ChatHistory): string[] => {
	const found: string[] = [];
	const seen = new Set<string>();
	for (const message of Object.values(history?.messages ?? {})) {
		for (const block of message.content_blocks ?? []) {
			if (block.type !== 'tool_calls') {
				continue;
			}
			for (const result of block.results ?? []) {
				const content = (result as { content?: unknown })?.content;
				// 生文字列を 'running' で先に絞る(無関係な result の JSON.parse を避ける)
				if (typeof content !== 'string' || !content.includes('running')) {
					continue;
				}
				try {
					const parsed = JSON.parse(content);
					if (
						parsed !== null &&
						typeof parsed === 'object' &&
						typeof parsed.ok === 'boolean' &&
						typeof parsed.session_id === 'string' &&
						parsed.answer === undefined &&
						parsed.status === 'running' &&
						!seen.has(parsed.session_id)
					) {
						seen.add(parsed.session_id);
						found.push(parsed.session_id);
					}
				} catch (e) {
					// JSON でない result は対象外
				}
			}
		}
	}
	return found;
};

/**
 * この session の resume が既に処理済みか(リロード・再マウントでの再検知を drop する)。
 * 処理済みの根拠はどちらも決定的:
 * - 定型 resume メッセージが履歴の user message にある(自動送信済み)
 * - answer 付きの subagent tool result が履歴にある(await_subagent / 同期継続で回収済み)
 */
export const isSubagentResumeHandled = (history: ChatHistory, sessionId: string): boolean => {
	if (!sessionId) {
		return true;
	}
	const resumeMessage = buildSubagentResumeMessage(sessionId);
	for (const message of Object.values(history?.messages ?? {})) {
		if (message.role === 'user' && message.content === resumeMessage) {
			return true;
		}
		for (const block of message.content_blocks ?? []) {
			if (block.type !== 'tool_calls') {
				continue;
			}
			for (const result of block.results ?? []) {
				const content = (result as { content?: unknown })?.content;
				// 生文字列を session_id で先に絞る(無関係な result の JSON.parse を避ける)
				if (typeof content !== 'string' || !content.includes(sessionId)) {
					continue;
				}
				try {
					const parsed = JSON.parse(content);
					if (
						parsed !== null &&
						typeof parsed === 'object' &&
						parsed.session_id === sessionId &&
						typeof parsed.answer === 'string'
					) {
						return true;
					}
				} catch (e) {
					// JSON でない result は対象外
				}
			}
		}
	}
	return false;
};
