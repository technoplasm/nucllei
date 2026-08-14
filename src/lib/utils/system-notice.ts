/**
 * llens(#110): システム挿入通知メッセージの判定。
 *
 * バックグラウンドタスクの配達通知は BE が user ロールで履歴へ直接書き、
 * `meta.system_notice` を立てる(サーバ駆動再開 — 契約 §M4)。判定はこの
 * フラグのみ — 文面プレフィクス判定は持たない(偽装・翻訳・書式変更に脆いため)。
 */
import type { ChatMessage } from '$lib/types/chat';

export const isSystemNoticeMessage = (message: ChatMessage | null | undefined): boolean =>
	message?.role === 'user' && !!message?.meta?.system_notice;
