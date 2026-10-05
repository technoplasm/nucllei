import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';
import isToday from 'dayjs/plugin/isToday';
import isYesterday from 'dayjs/plugin/isYesterday';
import localizedFormat from 'dayjs/plugin/localizedFormat';

dayjs.extend(relativeTime);
dayjs.extend(isToday);
dayjs.extend(isYesterday);
dayjs.extend(localizedFormat);

import { WEBUI_BASE_URL } from '$lib/constants';
import { TTS_RESPONSE_SPLIT } from '$lib/types';
import type { ChatHistory, ChatMessage } from '$lib/types/chat';

import { render } from '$lib/utils/markdown-it';

//////////////////////////
// Helper functions
//////////////////////////

export const replaceTokens = (
	content: string,
	sourceIds?: string[],
	char?: string,
	user?: string
) => {
	const tokens: {
		regex: RegExp;
		replacement: string | ((substring: string, ...args: string[]) => string) | undefined;
	}[] = [
		{ regex: /{{char}}/gi, replacement: char },
		{ regex: /{{user}}/gi, replacement: user },
		{
			regex: /{{VIDEO_FILE_ID_([a-f0-9-]+)}}/gi,
			replacement: (_, fileId) =>
				`<video src="${WEBUI_BASE_URL}/api/v1/files/${fileId}/content" controls></video>`
		},
		{
			regex: /{{HTML_FILE_ID_([a-f0-9-]+)}}/gi,
			// llens(GHSA-8gh5-qqh8-hq3x): iframe を sandbox 化(allow-same-origin は付けない)。
			// これにより枠内 HTML は opaque origin で動作し、親オリジンの localStorage トークン等に
			// 到達できず Stored XSS→乗っ取りが成立しない。onload の自動高さ調整は cross-origin で
			// 参照不可になるため撤去(DOMPurify でも onload は元々除去されていた)。
			replacement: (_, fileId) =>
				`<iframe src="${WEBUI_BASE_URL}/api/v1/files/${fileId}/content/html" width="100%" frameborder="0" sandbox="allow-scripts"></iframe>`
		}
	];

	// Replace tokens outside code blocks only
	const processOutsideCodeBlocks = (text: string, replacementFn: (segment: string) => string) => {
		return text
			.split(/(```[\s\S]*?```|`[\s\S]*?`)/)
			.map((segment) => {
				return segment.startsWith('```') || segment.startsWith('`')
					? segment
					: replacementFn(segment);
			})
			.join('');
	};

	// Apply replacements
	content = processOutsideCodeBlocks(content, (segment) => {
		tokens.forEach(({ regex, replacement }) => {
			if (replacement !== undefined && replacement !== null) {
				// 型都合の分岐のみ(String.replace のオーバーロードが string / 関数で分かれる)。挙動は同一。
				segment =
					typeof replacement === 'string'
						? segment.replace(regex, replacement)
						: segment.replace(regex, replacement);
			}
		});

		if (Array.isArray(sourceIds)) {
			sourceIds.forEach((sourceId, idx) => {
				const regex = new RegExp(`\\[${idx + 1}\\]`, 'g');
				segment = segment.replace(regex, `<source_id data="${idx + 1}" title="${sourceId}" />`);
			});
		}

		return segment;
	});

	return content;
};

export const sanitizeResponseContent = (content: string) => {
	return content
		.replace(/<\|[a-z]*$/, '')
		.replace(/<\|[a-z]+\|$/, '')
		.replace(/<$/, '')
		.replaceAll(/<\|[a-z]+\|>/g, ' ')
		.replaceAll('<', '&lt;')
		.replaceAll('>', '&gt;')
		.trim();
};

export const processResponseContent = (content: string) => {
	return content.trim();
};

export function unescapeHtml(html: string) {
	const doc = new DOMParser().parseFromString(html, 'text/html');
	return doc.documentElement.textContent;
}

export const splitStream = (splitOn: string) => {
	let buffer = '';
	return new TransformStream({
		transform(chunk, controller) {
			buffer += chunk;
			const parts = buffer.split(splitOn);
			parts.slice(0, -1).forEach((part) => controller.enqueue(part));
			buffer = parts[parts.length - 1];
		},
		flush(controller) {
			if (buffer) controller.enqueue(buffer);
		}
	});
};

export const canvasPixelTest = () => {
	// Test a 1x1 pixel to potentially identify browser/plugin fingerprint blocking or spoofing
	// Inspiration: https://github.com/kkapsner/CanvasBlocker/blob/master/test/detectionTest.js
	const canvas = document.createElement('canvas');
	const ctx = canvas.getContext('2d');
	if (!ctx) {
		// context が取れない(canvas ブロッカー等)= fingerprint 妨害とみなす
		return false;
	}
	canvas.height = 1;
	canvas.width = 1;
	const imageData = new ImageData(canvas.width, canvas.height);
	const pixelValues = imageData.data;

	// Generate RGB test data
	for (let i = 0; i < imageData.data.length; i += 1) {
		if (i % 4 !== 3) {
			pixelValues[i] = Math.floor(256 * Math.random());
		} else {
			pixelValues[i] = 255;
		}
	}

	ctx.putImageData(imageData, 0, 0);
	const p = ctx.getImageData(0, 0, canvas.width, canvas.height).data;

	// Read RGB data and fail if unmatched
	for (let i = 0; i < p.length; i += 1) {
		if (p[i] !== pixelValues[i]) {
			console.log(
				'canvasPixelTest: Wrong canvas pixel RGB value detected:',
				p[i],
				'at:',
				i,
				'expected:',
				pixelValues[i]
			);
			console.log('canvasPixelTest: Canvas blocking or spoofing is likely');
			return false;
		}
	}

	return true;
};

export const compressImage = async (
	imageUrl: string,
	maxDimension: number,
	mimeType: string = 'image/png'
): Promise<string> => {
	return new Promise((resolve, reject) => {
		const img = new Image();
		img.onload = () => {
			// 長辺が maxDimension に収まっていれば無加工
			if (Math.max(img.width, img.height) <= maxDimension) {
				resolve(imageUrl);
				return;
			}

			const scale = maxDimension / Math.max(img.width, img.height);
			const width = Math.round(img.width * scale);
			const height = Math.round(img.height * scale);

			const canvas = document.createElement('canvas');
			canvas.width = width;
			canvas.height = height;

			// フレッシュな canvas の 2d context は null にならない(型都合のアサーション)
			const context = canvas.getContext('2d')!;
			context.drawImage(img, 0, 0, width, height);

			// MIME は維持する(旧実装は無引数 toDataURL で常に PNG 化 — JPEG 写真が
			// 縮小したのに肥大化していた)。ブラウザ未対応の形式は PNG フォールバック。
			resolve(canvas.toDataURL(mimeType, 0.9));
		};
		img.onerror = (error) => reject(error);
		img.src = imageUrl;
	});
};
export const generateInitialsImage = (name: string | undefined) => {
	const canvas = document.createElement('canvas');
	const ctx = canvas.getContext('2d');
	canvas.width = 100;
	canvas.height = 100;

	if (!ctx || !canvasPixelTest()) {
		console.log(
			'generateInitialsImage: failed pixel test, fingerprint evasion is likely. Using default image.'
		);
		return '/user.png';
	}

	ctx.fillStyle = '#F39C12';
	ctx.fillRect(0, 0, canvas.width, canvas.height);

	ctx.fillStyle = '#FFFFFF';
	ctx.font = '40px Helvetica';
	ctx.textAlign = 'center';
	ctx.textBaseline = 'middle';

	const sanitizedName = (name ?? '').trim();
	const initials =
		sanitizedName.length > 0
			? sanitizedName[0] +
				(sanitizedName.split(' ').length > 1
					? sanitizedName[sanitizedName.lastIndexOf(' ') + 1]
					: '')
			: '';

	ctx.fillText(initials.toUpperCase(), canvas.width / 2, canvas.height / 2);

	return canvas.toDataURL();
};

// メッセージ見出しの日時。「今日」「昨日」は呼び手の i18n(`$i18n.t`)で訳す — ここは
// Svelte の store を読めないので translator を受け取る(省略時は英語のまま)
export const formatDate = (
	inputDate: string | number | Date,
	t: (key: string) => string = (key) => key
) => {
	const date = dayjs(inputDate);

	if (date.isToday()) {
		return `${t('Today')} ${date.format('LT')}`;
	} else if (date.isYesterday()) {
		return `${t('Yesterday')} ${date.format('LT')}`;
	} else {
		return `${date.format('L')} ${date.format('LT')}`;
	}
};

export const copyToClipboard = async (text: string, formatted = false): Promise<boolean> => {
	if (formatted) {
		const htmlContent = render(text);

		// Add basic styling to make the content look better when pasted
		const styledHtml = `
			<div>
				<style>
					pre {
						background-color: #f6f8fa;
						border-radius: 6px;
						padding: 16px;
						overflow: auto;
					}
					code {
						font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
						font-size: 14px;
					}
					.hljs-keyword { color: #d73a49; }
					.hljs-string { color: #032f62; }
					.hljs-comment { color: #6a737d; }
					.hljs-function { color: #6f42c1; }
					.hljs-number { color: #005cc5; }
					.hljs-operator { color: #d73a49; }
					.hljs-class { color: #6f42c1; }
					.hljs-title { color: #6f42c1; }
					.hljs-params { color: #24292e; }
					.hljs-built_in { color: #005cc5; }
					blockquote {
						border-left: 4px solid #dfe2e5;
						padding-left: 16px;
						color: #6a737d;
						margin-left: 0;
						margin-right: 0;
					}
					table {
						border-collapse: collapse;
						width: 100%;
						margin-bottom: 16px;
					}
					table, th, td {
						border: 1px solid #dfe2e5;
					}
					th, td {
						padding: 8px 12px;
					}
					th {
						background-color: #f6f8fa;
					}
				</style>
				${htmlContent}
			</div>
		`;

		// Create a blob with HTML content
		const blob = new Blob([styledHtml], { type: 'text/html' });

		try {
			// Create a ClipboardItem with HTML content
			const data = new ClipboardItem({
				'text/html': blob,
				'text/plain': new Blob([text], { type: 'text/plain' })
			});

			// Write to clipboard
			await navigator.clipboard.write([data]);
			return true;
		} catch (err) {
			console.error('Error copying formatted content:', err);
			// Fallback to plain text
			return await copyToClipboard(text);
		}
	} else {
		let result = false;
		if (!navigator.clipboard) {
			const textArea = document.createElement('textarea');
			textArea.value = text;

			// Avoid scrolling to bottom
			textArea.style.top = '0';
			textArea.style.left = '0';
			textArea.style.position = 'fixed';

			document.body.appendChild(textArea);
			textArea.focus();
			textArea.select();

			try {
				const successful = document.execCommand('copy');
				const msg = successful ? 'successful' : 'unsuccessful';
				console.log('Fallback: Copying text command was ' + msg);
				result = true;
			} catch (err) {
				console.error('Fallback: Oops, unable to copy', err);
			}

			document.body.removeChild(textArea);
			return result;
		}

		result = await navigator.clipboard
			.writeText(text)
			.then(() => {
				console.log('Async: Copying to clipboard was successful!');
				return true;
			})
			.catch((error) => {
				console.error('Async: Could not copy text: ', error);
				return false;
			});

		return result;
	}
};

export const extractCurlyBraceWords = (text: string) => {
	const regex = /\{\{([^}]+)\}\}/g;
	const matches = [];
	let match;

	while ((match = regex.exec(text)) !== null) {
		matches.push({
			word: match[1].trim(),
			startIndex: match.index,
			endIndex: regex.lastIndex - 1
		});
	}

	return matches;
};

export const removeLastWordFromString = (inputString: string, wordString: string) => {
	console.log('inputString', inputString);
	// Split the string by newline characters to handle lines separately
	const lines = inputString.split('\n');

	// Take the last line to operate only on it(split の結果は常に 1 要素以上 — ?? は型都合)
	const lastLine = lines.pop() ?? '';

	// Split the last line into an array of words
	const words = lastLine.split(' ');

	// Conditional to check for the last word removal
	if (words.at(-1) === wordString || (wordString === '' && words.at(-1) === '\\#')) {
		words.pop(); // Remove last word if condition is satisfied
	}

	// Join the remaining words back into a string and handle space correctly
	let updatedLastLine = words.join(' ');

	// Add a trailing space to the updated last line if there are still words
	if (updatedLastLine !== '') {
		updatedLastLine += ' ';
	}

	// Combine the lines together again, placing the updated last line back in
	const resultString = [...lines, updatedLastLine].join('\n');

	// Return the final string
	console.log('resultString', resultString);

	return resultString;
};

export const getImportOrigin = (_chats: object[]) => {
	// Check what external service chat imports are from
	if ('mapping' in _chats[0]) {
		return 'openai';
	}
	return 'webui';
};

// ChatGPT エクスポート(conversations.json)の最小形(実データからの逆算のみ)
type OpenAIImportMessage = {
	message?: {
		author?: { role?: string };
		content: { parts?: string[]; text?: string | null };
	} | null;
	children?: string[];
};

type OpenAIImportConvo = {
	id?: string;
	title?: string;
	create_time?: number;
	mapping?: Record<string, OpenAIImportMessage>;
};

const convertOpenAIMessages = (convo: OpenAIImportConvo) => {
	// Parse OpenAI chat messages and create chat dictionary for creating new chats
	// (mapping 不在なら for-in が 0 回で従来どおり — ?? {} は型都合)
	const mapping = convo['mapping'] ?? {};
	const messages = [];
	let currentId = '';
	let lastId: string | null = null;

	for (const message_id in mapping) {
		const message = mapping[message_id];
		currentId = message_id;
		try {
			if (
				messages.length == 0 &&
				(message['message'] == null ||
					(message['message']['content']['parts']?.[0] == '' &&
						message['message']['content']['text'] == null))
			) {
				// Skip chat messages with no content
				continue;
			} else {
				const new_chat = {
					id: message_id,
					parentId: lastId,
					childrenIds: message['children'] || [],
					role: message['message']?.['author']?.['role'] !== 'user' ? 'assistant' : 'user',
					content:
						message['message']?.['content']?.['parts']?.[0] ||
						message['message']?.['content']?.['text'] ||
						'',
					model: 'gpt-3.5-turbo',
					done: true,
					context: null
				};
				messages.push(new_chat);
				lastId = currentId;
			}
		} catch (error) {
			console.log('Error with', message, '\nError:', error);
		}
	}

	const history: Record<PropertyKey, (typeof messages)[number]> = {};
	messages.forEach((obj) => (history[obj.id] = obj));

	const chat = {
		history: {
			currentId: currentId,
			messages: history // Need to convert this to not a list and instead a json object
		},
		models: ['gpt-3.5-turbo'],
		messages: messages,
		options: {},
		timestamp: convo['create_time'],
		title: convo['title'] ?? 'New Chat'
	};
	return chat;
};

const validateChat = (chat: ReturnType<typeof convertOpenAIMessages>) => {
	// Because ChatGPT sometimes has features we can't use like DALL-E or might have corrupted messages, need to validate
	const messages = chat.messages;

	// Check if messages array is empty
	if (messages.length === 0) {
		return false;
	}

	// Last message's children should be an empty array
	const lastMessage = messages[messages.length - 1];
	if (lastMessage.childrenIds.length !== 0) {
		return false;
	}

	// First message's parent should be null
	const firstMessage = messages[0];
	if (firstMessage.parentId !== null) {
		return false;
	}

	// Every message's content should be a string
	for (const message of messages) {
		if (typeof message.content !== 'string') {
			return false;
		}
	}

	return true;
};

export const convertOpenAIChats = (_chats: OpenAIImportConvo[]) => {
	// Create a list of dictionaries with each conversation from import
	const chats = [];
	let failed = 0;
	for (const convo of _chats) {
		const chat = convertOpenAIMessages(convo);

		if (validateChat(chat)) {
			chats.push({
				id: convo['id'],
				user_id: '',
				title: convo['title'],
				chat: chat,
				timestamp: convo['create_time']
			});
		} else {
			failed++;
		}
	}
	console.log(failed, 'Conversations could not be imported');
	return chats;
};

export const removeEmojis = (str: string) => {
	// Regular expression to match emojis
	const emojiRegex = /[\uD800-\uDBFF][\uDC00-\uDFFF]|\uD83C[\uDC00-\uDFFF]|\uD83D[\uDC00-\uDE4F]/g;

	// Replace emojis with an empty string
	return str.replace(emojiRegex, '');
};

export const removeFormattings = (str: string) => {
	return (
		str
			// Block elements (remove completely)
			.replace(/(```[\s\S]*?```)/g, '') // Code blocks
			.replace(/^\|.*\|$/gm, '') // Tables
			// Inline elements (preserve content)
			.replace(/(?:\*\*|__)(.*?)(?:\*\*|__)/g, '$1') // Bold
			.replace(/(?:[*_])(.*?)(?:[*_])/g, '$1') // Italic
			.replace(/~~(.*?)~~/g, '$1') // Strikethrough
			.replace(/`([^`]+)`/g, '$1') // Inline code

			// Links and images
			.replace(/!?\[([^\]]*)\](?:\([^)]+\)|\[[^\]]*\])/g, '$1') // Links & images
			.replace(/^\[[^\]]+\]:\s*.*$/gm, '') // Reference definitions

			// Block formatting
			.replace(/^#{1,6}\s+/gm, '') // Headers
			.replace(/^\s*[-*+]\s+/gm, '') // Lists
			.replace(/^\s*(?:\d+\.)\s+/gm, '') // Numbered lists
			.replace(/^\s*>[> ]*/gm, '') // Blockquotes
			.replace(/^\s*:\s+/gm, '') // Definition lists

			// Cleanup
			.replace(/\[\^[^\]]*\]/g, '') // Footnotes
			.replace(/[-*_~]/g, '') // Remaining markers
			.replace(/\n{2,}/g, '\n')
	); // Multiple newlines
};

export const cleanText = (content: string) => {
	return removeFormattings(removeEmojis(content.trim()));
};

// This regular expression matches code blocks marked by triple backticks
const codeBlockRegex = /```[\s\S]*?```/g;

// 文の区切り(TTS の分割単位):
// - 半角 . ! ? の後の空白(英文 — 空白が無い "3.14" や "e.g.x" では切らない)
// - 全角 。！？ の直後(和文は空白を挟まない)。続く閉じ括弧「」』）」は前の文に含め、
//   「！？」のような連続は 1 つの区切りとして扱う
// - 改行(箇条書き・段落。和文は句点なしの見出しや箇条書きが多い)
const SENTENCE_BOUNDARY = /(?<=[.!?])\s+|(?<=[。！？][」』）]*)(?![。！？」』）])\s*|\s*\n\s*/;

// 和文(かな・漢字を含む)は空白で単語が区切られないため、単語数での短文判定が効かない
const CJK_REGEX = /[\u3040-\u30ff\u3400-\u9fff]/;

export const extractSentences = (text: string) => {
	const codeBlocks: string[] = [];
	let index = 0;

	// Temporarily replace code blocks with placeholders and store the blocks separately
	text = text.replace(codeBlockRegex, (match) => {
		const placeholder = `\u0000${index}\u0000`; // Use a unique placeholder
		codeBlocks[index++] = match;
		return placeholder;
	});

	// Split the modified text into sentences based on common punctuation marks, avoiding these blocks
	let sentences = text.split(SENTENCE_BOUNDARY);

	// Restore code blocks and process sentences
	sentences = sentences.map((sentence) => {
		// Check if the sentence includes a placeholder for a code block
		// eslint-disable-next-line no-control-regex -- \u0000 はコードブロック退避の意図的プレースホルダ
		return sentence.replace(/\u0000(\d+)\u0000/g, (_, idx) => codeBlocks[idx]);
	});

	return sentences.map(cleanText).filter(Boolean);
};

export const extractParagraphsForAudio = (text: string) => {
	const codeBlocks: string[] = [];
	let index = 0;

	// Temporarily replace code blocks with placeholders and store the blocks separately
	text = text.replace(codeBlockRegex, (match) => {
		const placeholder = `\u0000${index}\u0000`; // Use a unique placeholder
		codeBlocks[index++] = match;
		return placeholder;
	});

	// Split the modified text into paragraphs based on newlines, avoiding these blocks
	let paragraphs = text.split(/\n+/);

	// Restore code blocks and process paragraphs
	paragraphs = paragraphs.map((paragraph) => {
		// Check if the paragraph includes a placeholder for a code block
		// eslint-disable-next-line no-control-regex -- \u0000 はコードブロック退避の意図的プレースホルダ
		return paragraph.replace(/\u0000(\d+)\u0000/g, (_, idx) => codeBlocks[idx]);
	});

	return paragraphs.map(cleanText).filter(Boolean);
};

export const extractSentencesForAudio = (text: string) => {
	return extractSentences(text).reduce((mergedTexts, currentText) => {
		const lastIndex = mergedTexts.length - 1;
		if (lastIndex >= 0) {
			const previousText = mergedTexts[lastIndex];
			// 短すぎる断片は次の文と合わせて 1 リクエストにする(細切れの音声を避ける)。
			// 和文は単語数を見ず文字数だけで判定する(1 文字あたりの情報量が多いので閾値も小さく)
			const isShort = CJK_REGEX.test(previousText)
				? previousText.length < 20
				: previousText.split(/\s+/).length < 4 || previousText.length < 50;
			if (isShort) {
				// 和文の句点・閉じ括弧の後ろには空白を挟まない(「〜しました。」と…)
				const separator = /[。！？」』）]$/.test(previousText) ? '' : ' ';
				mergedTexts[lastIndex] = previousText + separator + currentText;
			} else {
				mergedTexts.push(currentText);
			}
		} else {
			mergedTexts.push(currentText);
		}
		return mergedTexts;
	}, [] as string[]);
};

export const getMessageContentParts = (content: string, split_on: string = 'punctuation') => {
	// content は text ブロックの join(#17 L0)— details 擬似 HTML は含まれない
	const messageContentParts: string[] = [];

	switch (split_on) {
		default:
		case TTS_RESPONSE_SPLIT.PUNCTUATION:
			messageContentParts.push(...extractSentencesForAudio(content));
			break;
		case TTS_RESPONSE_SPLIT.PARAGRAPHS:
			messageContentParts.push(...extractParagraphsForAudio(content));
			break;
		case TTS_RESPONSE_SPLIT.NONE:
			messageContentParts.push(cleanText(content));
			break;
	}

	return messageContentParts;
};

export const blobToFile = (blob: Blob, fileName: string) => {
	// Create a new File object from the Blob
	const file = new File([blob], fileName, { type: blob.type });
	return file;
};

export const getPromptVariables = (user_name?: string, user_location?: string) => {
	return {
		'{{USER_NAME}}': user_name,
		'{{USER_LOCATION}}': user_location || 'Unknown',
		'{{CURRENT_DATETIME}}': getCurrentDateTime(),
		'{{CURRENT_DATE}}': getFormattedDate(),
		'{{CURRENT_TIME}}': getFormattedTime(),
		'{{CURRENT_WEEKDAY}}': getWeekday(),
		'{{CURRENT_TIMEZONE}}': getUserTimezone(),
		'{{USER_LANGUAGE}}': localStorage.getItem('locale') || 'en-US'
	};
};

/**
 * @param {string} template - The template string containing placeholders.
 * @returns {string} The template string with the placeholders replaced by the prompt.
 */
export const promptTemplate = (
	template: string,
	user_name?: string,
	user_location?: string,
	base_model_id?: string
): string => {
	// Get the current date
	const currentDate = new Date();

	// Format the date to YYYY-MM-DD
	const formattedDate =
		currentDate.getFullYear() +
		'-' +
		String(currentDate.getMonth() + 1).padStart(2, '0') +
		'-' +
		String(currentDate.getDate()).padStart(2, '0');

	// Format the time to HH:MM:SS AM/PM
	const currentTime = currentDate.toLocaleTimeString('en-US', {
		hour: 'numeric',
		minute: 'numeric',
		second: 'numeric',
		hour12: true
	});

	// Get the current weekday
	const currentWeekday = getWeekday();

	// Get the user's timezone
	const currentTimezone = getUserTimezone();

	// Get the user's language
	const userLanguage = localStorage.getItem('locale') || 'en-US';

	// Replace {{CURRENT_DATETIME}} in the template with the formatted datetime
	template = template.replace('{{CURRENT_DATETIME}}', `${formattedDate} ${currentTime}`);

	// Replace {{CURRENT_DATE}} in the template with the formatted date
	template = template.replace('{{CURRENT_DATE}}', formattedDate);

	// Replace {{CURRENT_TIME}} in the template with the formatted time
	template = template.replace('{{CURRENT_TIME}}', currentTime);

	// Replace {{CURRENT_WEEKDAY}} in the template with the current weekday
	template = template.replace('{{CURRENT_WEEKDAY}}', currentWeekday);

	// Replace {{CURRENT_TIMEZONE}} in the template with the user's timezone
	template = template.replace('{{CURRENT_TIMEZONE}}', currentTimezone);

	// Replace {{USER_LANGUAGE}} in the template with the user's language
	template = template.replace('{{USER_LANGUAGE}}', userLanguage);

	if (user_name) {
		// Replace {{USER_NAME}} in the template with the user's name
		template = template.replace('{{USER_NAME}}', user_name);
	}

	if (user_location) {
		// Replace {{USER_LOCATION}} in the template with the current location
		template = template.replace('{{USER_LOCATION}}', user_location);
	} else {
		// Replace {{USER_LOCATION}} in the template with 'Unknown' if no location is provided
		template = template.replace('{{USER_LOCATION}}', 'LOCATION_UNKNOWN');
	}

	// 実際に推論する base model の id(BE nucllei/utils/task.py の prompt_template /
	// resolve_base_model_id と同じ定義)
	template = template.replace('{{BASE_MODEL_ID}}', base_model_id || 'Unknown');

	return template;
};

export const getTimeRange = (timestamp: number) => {
	const now = new Date();
	const date = new Date(timestamp * 1000); // Convert Unix timestamp to milliseconds

	// Calculate the difference in milliseconds
	const diffTime = now.getTime() - date.getTime();
	const diffDays = diffTime / (1000 * 3600 * 24);

	const nowDate = now.getDate();
	const nowMonth = now.getMonth();
	const nowYear = now.getFullYear();

	const dateDate = date.getDate();
	const dateMonth = date.getMonth();
	const dateYear = date.getFullYear();

	if (nowYear === dateYear && nowMonth === dateMonth && nowDate === dateDate) {
		return 'Today';
	} else if (nowYear === dateYear && nowMonth === dateMonth && nowDate - dateDate === 1) {
		return 'Yesterday';
	} else if (diffDays <= 7) {
		return 'Previous 7 days';
	} else if (diffDays <= 30) {
		return 'Previous 30 days';
	} else if (nowYear === dateYear) {
		return date.toLocaleString('default', { month: 'long' });
	} else {
		return date.getFullYear().toString();
	}
};

// Function to determine the best matching language
export const bestMatchingLanguage = (
	supportedLanguages: { code: string }[],
	preferredLanguages: readonly string[],
	defaultLocale: string
) => {
	const languages = supportedLanguages.map((lang) => lang.code);

	const match = preferredLanguages
		.map((prefLang) => languages.find((lang) => lang.startsWith(prefLang)))
		.find(Boolean);

	return match || defaultLocale;
};

// Get the date in the format YYYY-MM-DD
export const getFormattedDate = () => {
	const date = new Date();
	const year = date.getFullYear();
	const month = String(date.getMonth() + 1).padStart(2, '0');
	const day = String(date.getDate()).padStart(2, '0');
	return `${year}-${month}-${day}`;
};

// Get the time in the format HH:MM:SS
export const getFormattedTime = () => {
	const date = new Date();
	return date.toTimeString().split(' ')[0];
};

// Get the current date and time in the format YYYY-MM-DD HH:MM:SS
export const getCurrentDateTime = () => {
	return `${getFormattedDate()} ${getFormattedTime()}`;
};

// Get the user's timezone
export const getUserTimezone = () => {
	return Intl.DateTimeFormat().resolvedOptions().timeZone;
};

// Get the weekday
export const getWeekday = () => {
	const date = new Date();
	const weekdays = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
	return weekdays[date.getDay()];
};

export const createMessagesList = (
	history: ChatHistory,
	messageId: string | null
): ChatMessage[] => {
	if (messageId === null) {
		return [];
	}

	// 親方向へ反復で辿る(再帰だと深い履歴でスタックを食い、parentId が循環した
	// 壊れた履歴では無限再帰で落ちる)。訪問済みに戻ったらそこで打ち切る
	const list: ChatMessage[] = [];
	const visited = new Set<string>();
	let id: string | null | undefined = messageId;
	while (id && !visited.has(id)) {
		visited.add(id);
		const message: ChatMessage | undefined = history.messages[id];
		if (!message) {
			break;
		}
		list.push(message);
		id = message.parentId;
	}
	return list.reverse();
};

export const formatFileSize = (size: unknown) => {
	if (size == null) return 'Unknown size';
	if (typeof size !== 'number' || size < 0) return 'Invalid size';
	if (size === 0) return '0 B';
	const units = ['B', 'KB', 'MB', 'GB', 'TB'];
	let unitIndex = 0;

	while (size >= 1024 && unitIndex < units.length - 1) {
		size /= 1024;
		unitIndex++;
	}
	return `${size.toFixed(1)} ${units[unitIndex]}`;
};

// OpenAPI spec / 生成する JSON Schema の最小形(実使用フィールドからの逆算のみ)
type OpenApiSchema = {
	$ref?: string;
	type?: string;
	description?: string;
	required?: string[];
	properties?: Record<string, OpenApiSchema>;
	items?: OpenApiSchema;
};

type JsonSchema = {
	type?: string;
	description?: string;
	properties?: Record<string, JsonSchema>;
	required?: string[];
	items?: JsonSchema;
};

type OpenApiOperation = {
	operationId?: string;
	summary?: string;
	description?: string;
	parameters?: { name: string; required?: boolean; schema: OpenApiSchema }[];
	requestBody?: { content?: Record<string, { schema?: OpenApiSchema }> };
};

type OpenApiSpec = {
	paths: Record<string, Record<string, OpenApiOperation>>;
	components: { schemas: Record<string, OpenApiSchema> };
};

// Helper function to recursively resolve OpenAPI schema into JSON schema format
function resolveSchema(
	schemaRef: OpenApiSchema | undefined,
	components: OpenApiSpec['components'],
	resolvedSchemas = new Set<string>()
): JsonSchema {
	if (!schemaRef) return {};

	if (schemaRef['$ref']) {
		const refPath = schemaRef['$ref'];
		// '/' split の結果は常に 1 要素以上 — ?? は型都合
		const schemaName = refPath.split('/').pop() ?? '';

		if (resolvedSchemas.has(schemaName)) {
			// Avoid infinite recursion on circular references
			return {};
		}
		resolvedSchemas.add(schemaName);
		const referencedSchema = components.schemas[schemaName];
		return resolveSchema(referencedSchema, components, resolvedSchemas);
	}

	if (schemaRef.type) {
		const schemaObj: JsonSchema = { type: schemaRef.type };

		if (schemaRef.description) {
			schemaObj.description = schemaRef.description;
		}

		switch (schemaRef.type) {
			case 'object':
				schemaObj.properties = {};
				schemaObj.required = schemaRef.required || [];
				for (const [propName, propSchema] of Object.entries(schemaRef.properties || {})) {
					schemaObj.properties[propName] = resolveSchema(propSchema, components);
				}
				break;

			case 'array':
				schemaObj.items = resolveSchema(schemaRef.items, components);
				break;

			default:
				// for primitive types (string, integer, etc.), just use as is
				break;
		}
		return schemaObj;
	}

	// fallback for schemas without explicit type
	return {};
}

// Main conversion function
export const convertOpenApiToToolPayload = (openApiSpec: OpenApiSpec) => {
	const toolPayload: {
		type: string;
		name: string | undefined;
		description: string;
		parameters: JsonSchema;
	}[] = [];

	for (const [_path, methods] of Object.entries(openApiSpec.paths)) {
		for (const [_method, operation] of Object.entries(methods)) {
			const tool: (typeof toolPayload)[number] = {
				type: 'function',
				name: operation.operationId,
				description: operation.description || operation.summary || 'No description available.',
				parameters: {
					type: 'object',
					properties: {},
					required: []
				}
			};

			// Extract path and query parameters
			if (operation.parameters) {
				operation.parameters.forEach((param) => {
					// properties / required は初期値で必ず存在(! は型都合)
					tool.parameters.properties![param.name] = {
						type: param.schema.type,
						description: param.schema.description || ''
					};

					if (param.required) {
						tool.parameters.required!.push(param.name);
					}
				});
			}

			// Extract and recursively resolve requestBody if available
			if (operation.requestBody) {
				const content = operation.requestBody.content;
				if (content && content['application/json']) {
					const requestSchema = content['application/json'].schema;
					const resolvedRequestSchema = resolveSchema(requestSchema, openApiSpec.components);

					if (resolvedRequestSchema.properties) {
						tool.parameters.properties = {
							...tool.parameters.properties,
							...resolvedRequestSchema.properties
						};

						if (resolvedRequestSchema.required) {
							tool.parameters.required = [
								...new Set([...(tool.parameters.required ?? []), ...resolvedRequestSchema.required])
							];
						}
					} else if (resolvedRequestSchema.type === 'array') {
						tool.parameters = resolvedRequestSchema; // special case when root schema is an array
					}
				}
			}

			toolPayload.push(tool);
		}
	}

	return toolPayload;
};
