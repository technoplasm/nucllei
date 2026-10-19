// 過去ターンの tool call/result をモデル向け履歴に復元する(#18-2)。
//
// フロントは従来 assistant を {role, content} に縮約して送っており、tool 往復が
// 次ターン以降のモデルから消えていた(「さっきの結果を使って」が壊れる)。
// content_blocks(#17 L0 で永続化済み)から、バックエンドの
// convert_content_blocks_to_messages と同じ wire 形に展開する:
//   assistant(text?, tool_calls) → tool(result)... → user(tool 結果の画像)? → assistant(text)
//
// - tool 結果の画像(results[].context_images、`__feed_image__`)は tool メッセージ群の
//   直後に image_url を持つ user メッセージとして再送する(#142 — 次ターンでモデルが
//   画像を忘れない)。エントリは参照文字列(file id / スキーム付き URL / "/" 始まり)で、
//   実体化(base64)は送信直前の backend inline_internal_image_urls。形はバックエンドと
//   共有 fixture(nucllei/test/fixtures/history_wire/context_images.json)で固定している
// - reasoning はモデルへ再送しない(ターンをまたぐ履歴 — バックエンドの続きターン・carried 計測と同じ。
//   reasoning_content を載せ直すのは同じターンの tool ループ追撃だけで、それは BE が組む — README_API 4.3)
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
	results?: { tool_call_id: string; content?: string; context_images?: string[] }[];
	synthetic?: boolean;
};

type WireContentPart =
	{ type: 'text'; text: string } | { type: 'image_url'; image_url: { url: string } };

export type WireMessage = {
	role: string;
	content: string | WireContentPart[];
	tool_calls?: {
		id: string;
		type: string;
		function: { name: string; arguments: string };
	}[];
	tool_call_id?: string;
};

// tool 結果の画像を運ぶ user メッセージの text パート(content_blocks.py と同文)
const TOOL_RESULT_IMAGES_TEXT =
	'(以下の画像はユーザーの添付ではない — 直前の tool の結果として読み込んだ画像)';

// context_images のエントリ → image_url の url(content_blocks.py と同分岐):
// スキーム付き URL と "/" 始まり(fs 参照・"/api/v1/files/{id}/content#page=N")は
// そのまま、それ以外は file id。
const contextImageUrl = (entry: string): string =>
	entry.startsWith('/') || /^https?:\/\//i.test(entry) ? entry : `/api/v1/files/${entry}/content`;

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
			// 中断で途切れたブロックの後始末(実測 400: SGLang は履歴の
			// tool_calls.arguments をパースするため、途中で切れた壊れ JSON を
			// 再送すると "unexpected end of data" で拒否される):
			// - arguments が壊れた call は履歴から落とす(実行されていない)
			// - 完全な call は残し、result 未着なら合成 result を対にする
			//   (tool_calls に tool 応答が無い履歴も不正になるため)
			const results = block.results ?? [];
			const sendableCalls = (block.calls ?? []).filter((call) => {
				const args = call.function?.arguments ?? '';
				if (args.trim() === '') {
					return true;
				}
				try {
					JSON.parse(args);
					return true;
				} catch {
					return false;
				}
			});
			if (sendableCalls.length === 0) {
				// 全滅(中断直後など)はブロックごとスキップ — text は前後に合流
				continue;
			}
			messages.push({
				role: 'assistant',
				content: textBuffer.join('\n\n'),
				// render/index 等の内部キーをモデルに漏らさず OpenAI 形だけ綴じ込む
				// (バックエンド convert_content_blocks_to_messages と同じ規律)
				tool_calls: sendableCalls.map((call) => ({
					id: call.id,
					type: call.type ?? 'function',
					function: {
						name: call.function?.name ?? '',
						arguments: call.function?.arguments ?? ''
					}
				}))
			});
			textBuffer = [];
			const imageUrls: string[] = [];
			for (const call of sendableCalls) {
				const result = results.find((r) => r.tool_call_id === call.id);
				messages.push({
					role: 'tool',
					tool_call_id: call.id,
					content: result
						? (result.content ?? '')
						: '[interrupted: this tool call produced no result]'
				});
				imageUrls.push(...(result?.context_images ?? []).map(contextImageUrl));
			}
			if (imageUrls.length > 0) {
				// role:tool の content は文字列契約で画素を運べないため user ロールで挟む
				messages.push({
					role: 'user',
					content: [
						{ type: 'text', text: TOOL_RESULT_IMAGES_TEXT },
						...imageUrls.map((url) => ({ type: 'image_url' as const, image_url: { url } }))
					]
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

/**
 * チャット履歴(createMessagesList の出力)+ 任意の system 文を、
 * OpenAI 互換 wire のメッセージ配列へ組み立てる(Chat.svelte の送信経路)。
 *
 * - user メッセージの画像 file は image_url パートに展開する(本文が空なら text パートは付けない)。
 *   llens(#69): url は正準形 "/api/v1/files/{id}/content"。送信直前に backend の
 *   inline_internal_image_urls が base64 化する(推論エンジンは /content に到達できない)。
 * - assistant は過去ターンの tool 往復を wire 形に復元(#18-2)。tool 結果の画像
 *   (context_images)も image_url を持つ user メッセージとして再送する(#142)。
 *   tool_calls ブロックが無ければ従来どおり縮約 1 メッセージ。
 * - 空 content のメッセージは落とす(user / tool / tool_calls 持ちは温存)。
 */
/** flatMap 各分岐の受け皿(text / image_url パート混在のため content は unknown)。 */
type OutboundWireMessage = {
	role: string;
	content: unknown;
	tool_calls?: WireMessage['tool_calls'];
	tool_call_id?: string;
};

export const buildWireMessages = (
	messagesList: any[],
	systemContent: string | null | undefined
): any[] => {
	return [systemContent ? { role: 'system', content: systemContent } : undefined, ...messagesList]
		.filter((message) => message)
		.flatMap((message): OutboundWireMessage[] => {
			if (
				message.role === 'user' &&
				(message.files ?? []).some((file: any) => file.type === 'image')
			) {
				return [
					{
						role: 'user',
						content: [
							// 画像だけの発話(本文なし)に空の text パートを付けない —
							// 空 text を弾く推論エンジン・テンプレートがある
							...((message.content ?? '').trim()
								? [
										{
											type: 'text',
											text: message.content
										}
									]
								: []),
							...message.files
								.filter((file: any) => file.type === 'image')
								.map((file: any) => ({
									type: 'image_url',
									image_url: {
										url: file.url
									}
								}))
						]
					}
				];
			}
			if (message.role === 'assistant') {
				return expandAssistantToolHistory(message.content, message?.content_blocks);
			}
			return [{ role: message.role, content: message.content }];
		})
		.filter(
			(message) =>
				message?.role === 'user' ||
				message?.role === 'tool' ||
				(message?.tool_calls?.length ?? 0) > 0 ||
				(typeof message?.content === 'string' && message.content.trim())
		);
};

/**
 * stop パラメータを wire の string[] へ。chat params はカンマ区切り文字列、
 * settings 由来は配列のことがある — どちらも受けて、"\n" 等のエスケープ表記を
 * 実文字へ戻す(upstream 由来の JSON.parse デコード)。
 *
 * ※ upstream は params.stop 不在 + settings.params.stop 在で
 *   `params?.stop.split(',')` が TypeError になる潜在バグがあった — 統一処理で解消。
 */
export const parseStopSequences = (
	stop: string | string[] | null | undefined
): string[] | undefined => {
	if (stop === undefined || stop === null || stop === '') {
		return undefined;
	}
	const tokens = typeof stop === 'string' ? stop.split(',').map((token) => token.trim()) : stop;
	return tokens.map((str) => decodeURIComponent(JSON.parse('"' + str.replace(/"/g, '\\"') + '"')));
};
