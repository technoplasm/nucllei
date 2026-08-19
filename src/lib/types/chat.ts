/**
 * チャット履歴(メッセージ樹)の共有型。
 *
 * Chat.svelte が生成・変異し、Messages 樹(Messages / Message / UserMessage /
 * ResponseMessage / MultiResponseMessages …)が参照する history の形を一元定義する。
 * フィールドは実使用箇所からの逆算のみ — 実データに無いフィールドを足さない。
 *
 * ストリーミング中の変異規約は $lib/utils/chat-events.ts(applyCompletionData /
 * applyMessageMutation)を参照。ContentBlock はそちらが真実源(ここでは再 export)。
 */

import type { ContentBlock } from '$lib/utils/chat-events';

export type { ContentBlock };

/** message.files の 1 要素(MessageInput の uploadFileHandler / library 取り込みが生成)。 */
export interface ChatFile {
	type: string; // 'image' | 'file' など
	id?: string | null;
	url?: string;
	name?: string;
	size?: number;
	status?: string; // 'uploading' | 'uploaded'
	error?: string;
	itemId?: string;
	collection_name?: string;
	context?: string; // fullContext 指定時 'full'
	origin?: string; // library 取り込みは 'library'
	/** アップロード API のレスポンス実体(ChatFiles.svelte が meta.name / meta.size / filename を参照)。 */
	file?: {
		meta?: { name?: string; size?: number; [key: string]: unknown };
		filename?: string;
		[key: string]: unknown;
	};
}

/** citation/source イベントの 1 件(Citations.svelte が集約表示)。 */
export interface MessageSource {
	source?: { id?: string; name?: string; url?: string; [key: string]: unknown };
	document?: string[];
	metadata?: Record<string, unknown>[];
	distances?: number[];
}

/** code_execution source イベント(id で upsert — chat-events.ts)。 */
export interface CodeExecution {
	id: string;
	name?: string;
	code?: string;
	language?: string;
	result?: {
		error?: string;
		output?: string;
		files?: { name: string; url: string }[];
	};
}

export interface ChatMessage {
	id: string;
	parentId: string | null;
	childrenIds: string[];
	role: string; // 'user' | 'assistant'(addMessages 経由は任意文字列があり得る)
	content: string;
	/** Unix epoch(秒)。submitMessage(Chat.svelte)経由の user message には無い。 */
	timestamp?: number;

	/** assistant: ブロック列(#17 L0 — 表示・履歴復元の真実源)。 */
	content_blocks?: ContentBlock[];

	// assistant のモデル情報
	model?: string;
	modelName?: string;
	selectedModelId?: string;

	// user 側
	/** モデル発話をユーザー側に置く場合のモデル id(UserMessage のプロフィール表示)。 */
	user?: string;

	files?: ChatFile[];

	// ストリーミング状態
	done?: boolean;
	error?: boolean | { content: string };
	/** ユーザー中断(task-cancelled)。completed outlet へ成否として伝搬。 */
	interrupted?: boolean;

	sources?: MessageSource[];
	code_executions?: CodeExecution[];
	usage?: Record<string, unknown>;

	/** completed(outlet)応答の messages が持ち得る付帯情報。FE は読まず wire へ素通しする。 */
	info?: unknown;

	/**
	 * llens(#110): BE が履歴へ直接書くメッセージの機械可読フラグ。
	 * system_notice = バックグラウンドタスクの配達通知(user ロール・サーバ挿入)。
	 * FE の描画分岐(SystemNoticeMessage)の唯一の根拠 — 文面プレフィクス判定はしない。
	 */
	meta?: { system_notice?: string };

	/** filter outlet が content を書き換えたときの原文(Chat.svelte)。 */
	originalContent?: string;
	/** TTS 自動読み上げの送出済み末尾文(Chat.svelte)。 */
	lastSentence?: string;
	/** 書き込みのみの遺構(Chat.svelte が null を代入、読み手なし)。 */
	userContext?: unknown;
}

export interface ChatHistory {
	messages: Record<string, ChatMessage>;
	currentId: string | null;
}
