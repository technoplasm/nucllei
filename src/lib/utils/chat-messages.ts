// 過去ターンの tool call/result をモデル向け履歴に復元する(#18-2)。
//
// フロントは従来 assistant を {role, content} に縮約して送っており、tool 往復が
// 次ターン以降のモデルから消えていた(「さっきの結果を使って」が壊れる)。
// content_blocks(#17 L0 で永続化済み)から、バックエンドの
// convert_content_blocks_to_messages と同じ wire 形に展開する:
//   assistant(text?, tool_calls) → tool(result)... → assistant(text)
//
// - reasoning はモデルへ再送しない(バックエンドと同じ規律)
// - synthetic な tool_calls(`/command` 発火の合成 exchange、#35)はスキップする
//   — 実体は inlet が毎リクエスト再注入するため、ここで送ると二重になる

type ContentBlock = {
	type: string;
	content?: string;
	calls?: {
		id: string;
		type?: string;
		function?: { name?: string; arguments?: string };
	}[];
	results?: { tool_call_id: string; content?: string }[];
	synthetic?: boolean;
};

export type WireMessage = {
	role: string;
	content: string;
	tool_calls?: {
		id: string;
		type: string;
		function: { name: string; arguments: string };
	}[];
	tool_call_id?: string;
};

export const expandAssistantToolHistory = (
	content: string,
	blocks: ContentBlock[] | null | undefined
): WireMessage[] => {
	const toolBlocks = (blocks ?? []).filter((b) => b.type === 'tool_calls' && !b.synthetic);
	if (toolBlocks.length === 0) {
		// tool 往復なし: 従来どおり縮約 1 メッセージ
		return [{ role: 'assistant', content }];
	}

	const messages: WireMessage[] = [];
	let textBuffer: string[] = [];

	for (const block of blocks ?? []) {
		if (block.type === 'text' && block.content) {
			textBuffer.push(block.content);
		} else if (block.type === 'tool_calls') {
			if (block.synthetic) {
				continue;
			}
			messages.push({
				role: 'assistant',
				content: textBuffer.join('\n\n'),
				// render/index 等の内部キーをモデルに漏らさず OpenAI 形だけ綴じ込む
				// (バックエンド convert_content_blocks_to_messages と同じ規律)
				tool_calls: (block.calls ?? []).map((call) => ({
					id: call.id,
					type: call.type ?? 'function',
					function: {
						name: call.function?.name ?? '',
						arguments: call.function?.arguments ?? ''
					}
				}))
			});
			textBuffer = [];
			for (const result of block.results ?? []) {
				messages.push({
					role: 'tool',
					tool_call_id: result.tool_call_id,
					content: result.content ?? ''
				});
			}
		}
		// reasoning は再送しない
	}

	const trailing = textBuffer.join('\n\n');
	if (trailing) {
		messages.push({ role: 'assistant', content: trailing });
	}
	return messages;
};
