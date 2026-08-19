/**
 * chat completion イベント(wire v2)の message への適用 — 純関数層。
 *
 * バックエンド(nucllei/utils/middleware.py consume_model_stream)の emit 規約:
 * - ストリーム開始時に無条件スナップショット {content, content_blocks}
 * - 純追記は {delta: {block: 'text'|'reasoning', content}}(高頻度・小粒)
 * - 構造変化(ブロック開閉・tool_calls)とテキスト連続中の間引き(約 2 秒)は
 *   スナップショット
 * - 完了は {done: true, content, content_blocks, title}
 * - エラーは {error: {content}} の一形のみ
 *
 * ここは I/O・store・DOM を持たない(TTS 発話・トースト・保存は呼び出し側)。
 * vitest(chat-events.test.ts)と BE リプレイ fixture で契約を固定する。
 */

export interface CompletionDelta {
	block: 'text' | 'reasoning';
	content: string;
}

export interface CompletionData {
	delta?: CompletionDelta;
	content?: string;
	content_blocks?: ContentBlock[];
	done?: boolean;
	title?: string;
	error?: { content: string };
	usage?: Record<string, unknown>;
	sources?: unknown[];
}

export interface ContentBlock {
	type: 'text' | 'reasoning' | 'tool_calls';
	content?: string;
	done?: boolean;
	duration?: number;
	calls?: unknown[];
	results?: unknown[];
	synthetic?: boolean;
}

export interface ApplyResult {
	/** text 本文が変化した(TTS 文分割・自動スクロールのトリガ) */
	contentChanged: boolean;
	/** この data で done が立った */
	done: boolean;
	/** error が届いた(呼び出し側でトースト等) */
	error: boolean;
}

/**
 * chat:completion イベントの data を message に適用する(mutate)。
 *
 * delta の適用先が無い(スナップショット未達の途中参加等)場合はブロックを
 * 新設して追記する — 直後に届く間引きスナップショットで全体が上書き収束する。
 */
export function applyCompletionData(
	message: Record<string, any>,
	data: CompletionData
): ApplyResult {
	const result: ApplyResult = { contentChanged: false, done: false, error: false };

	if (data.error) {
		message.error = { content: data.error.content };
		result.error = true;
	}

	if (data.sources) {
		message.sources = data.sources;
	}

	if (data.content_blocks) {
		// 累積スナップショット(表示の真実源)— 丸ごと差し替え
		message.content_blocks = data.content_blocks;
	}

	if (data.content !== undefined) {
		// スナップショット同梱の派生 content(text ブロックの join)
		if (message.content !== data.content) {
			message.content = data.content;
			result.contentChanged = true;
		}
	}

	if (data.delta) {
		applyDelta(message, data.delta);
		if (data.delta.block === 'text') {
			result.contentChanged = true;
		}
	}

	if (data.usage) {
		message.usage = data.usage;
	}

	if (data.done) {
		message.done = true;
		result.done = true;
	}

	return result;
}

function applyDelta(message: Record<string, any>, delta: CompletionDelta) {
	if (!Array.isArray(message.content_blocks)) {
		message.content_blocks = [];
	}
	const blocks: ContentBlock[] = message.content_blocks;
	let tail = blocks.at(-1);

	if (delta.block === 'reasoning') {
		if (!tail || tail.type !== 'reasoning' || tail.done) {
			tail = { type: 'reasoning', content: '', done: false };
			blocks.push(tail);
		}
		tail.content = (tail.content ?? '') + delta.content;
	} else {
		if (!tail || tail.type !== 'text') {
			tail = { type: 'text', content: '' };
			blocks.push(tail);
		}
		tail.content = (tail.content ?? '') + delta.content;
		// content は text ブロックの派生値。複数 text ブロック時の区切り(\n\n)の
		// 厳密な一致はスナップショットが担う — ここは素朴追記でよい
		// (content_blocks があるとき表示は blocks 側を使う)。
		message.content = (message.content ?? '') + delta.content;
	}
	// Svelte のリアクティビティ(再代入検知)のため配列参照を更新する
	message.content_blocks = blocks;
}

/**
 * chat:completion 以外の「message の状態だけを変える」イベントの適用(mutate)。
 * 適用したら true、ここの管轄外(chat:completion / chat:title / notification /
 * confirmation / input などの副作用イベント)は false を返し呼び出し側に委ねる。
 */
export function applyMessageMutation(
	type: string | null,
	data: any,
	message: Record<string, any>
): boolean {
	if (type === 'task-cancelled') {
		// llens: 中断された応答を done 化してスピナーを止める。interrupted は
		// 直後に届く done イベント → completed(outlet filter)経路で成否として
		// 伝搬・永続化される(backend は本イベントの後に done+blocks を emit する)
		message.done = true;
		message.interrupted = true;
		return true;
	}

	if (type === 'chat:message:delta' || type === 'message') {
		message.content += data.content;
		return true;
	}

	if (type === 'chat:message' || type === 'replace') {
		message.content = data.content;
		return true;
	}

	if (type === 'chat:message:files' || type === 'files') {
		message.files = data.files;
		return true;
	}

	if (type === 'source' || type === 'citation') {
		if (data?.type === 'code_execution') {
			// code execution は id で upsert
			if (!message?.code_executions) {
				message.code_executions = [];
			}

			const existingCodeExecutionIndex = message.code_executions.findIndex(
				(execution: any) => execution.id === data.id
			);

			if (existingCodeExecutionIndex !== -1) {
				message.code_executions[existingCodeExecutionIndex] = data;
			} else {
				message.code_executions.push(data);
			}
		} else {
			// Regular source.
			if (message?.sources) {
				message.sources.push(data);
			} else {
				message.sources = [data];
			}
		}
		return true;
	}

	return false;
}
