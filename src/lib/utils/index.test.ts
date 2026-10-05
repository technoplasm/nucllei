// utils/index.ts のうち、壊れた入力・言語差で落ちる/効かないと分かった箇所の回帰テスト。
// - createMessagesList: parentId が循環した履歴で無限再帰して FE が落ちた
// - TTS の文分割: 和文は「。」の後に空白が無いため 1 文にもならず、全文を 1 リクエストで送っていた

import { describe, it, expect } from 'vitest';

import { createMessagesList, extractSentencesForAudio, getMessageContentParts } from './index';
import type { ChatHistory, ChatMessage } from '$lib/types/chat';

const msg = (id: string, parentId: string | null, childrenIds: string[] = []): ChatMessage => ({
	id,
	parentId,
	childrenIds,
	role: 'user',
	content: id
});

const historyOf = (messages: ChatMessage[], currentId: string | null): ChatHistory => ({
	messages: Object.fromEntries(messages.map((m) => [m.id, m])),
	currentId
});

describe('createMessagesList', () => {
	it('root から指定 id までを古い順に返す', () => {
		const history = historyOf(
			[msg('u1', null, ['a1']), msg('a1', 'u1', ['u2']), msg('u2', 'a1', [])],
			'u2'
		);
		expect(createMessagesList(history, 'u2').map((m) => m.id)).toEqual(['u1', 'a1', 'u2']);
		expect(createMessagesList(history, 'a1').map((m) => m.id)).toEqual(['u1', 'a1']);
	});

	it('null は空、存在しない id は空(undefined を混ぜない)', () => {
		const history = historyOf([msg('u1', null)], 'u1');
		expect(createMessagesList(history, null)).toEqual([]);
		expect(createMessagesList(history, 'missing')).toEqual([]);
	});

	it('途中の親が欠けていたらそこで打ち切る', () => {
		const history = historyOf([msg('a1', 'gone'), msg('u2', 'a1')], 'u2');
		expect(createMessagesList(history, 'u2').map((m) => m.id)).toEqual(['a1', 'u2']);
	});

	it('parentId が循環していても止まり、各メッセージは 1 回だけ出る', () => {
		// u1 ⇄ a1(壊れた履歴)
		const history = historyOf([msg('u1', 'a1', ['a1']), msg('a1', 'u1', ['u1'])], 'a1');
		expect(createMessagesList(history, 'a1').map((m) => m.id)).toEqual(['u1', 'a1']);

		// 自己参照
		const self = historyOf([msg('x', 'x')], 'x');
		expect(createMessagesList(self, 'x').map((m) => m.id)).toEqual(['x']);
	});

	it('深い履歴でもスタックを溢れさせない', () => {
		const messages: ChatMessage[] = [];
		const depth = 20000;
		for (let i = 0; i < depth; i++) {
			messages.push(msg(`m${i}`, i === 0 ? null : `m${i - 1}`));
		}
		const history = historyOf(messages, `m${depth - 1}`);
		const list = createMessagesList(history, `m${depth - 1}`);
		expect(list).toHaveLength(depth);
		expect(list[0].id).toBe('m0');
	});
});

describe('TTS の文分割(extractSentencesForAudio)', () => {
	const ja =
		'はい、承知しました。以下に要点をまとめます。\n\n' +
		'- 第一に、患者の同意が必要です。\n' +
		'- 第二に、記録を残してください。\n' +
		'- 第三に、上長に報告します。\n\n' +
		'ご不明点があればお知らせください。';

	it('和文を「。」と改行で分割する(全文 1 リクエストにしない)', () => {
		const parts = extractSentencesForAudio(ja);
		expect(parts.length).toBeGreaterThan(1);
		// どの断片も元の文を途中で切っていない
		for (const part of parts) {
			expect(part.endsWith('。')).toBe(true);
		}
		// 本文は欠けない(結合の空白を除けば元の文がすべて入っている)
		const joined = parts.join('');
		for (const sentence of [
			'はい、承知しました。',
			'患者の同意が必要です。',
			'お知らせください。'
		]) {
			expect(joined).toContain(sentence);
		}
	});

	it('「！」「？」でも分割し、閉じ括弧と連続記号は前の文に付ける', () => {
		const text =
			'本当に実行してよろしいですか？取り消しはできません！' +
			'担当者は「確認しました。」と答えました。それで終わりですか！？次へ進みます。';
		const parts = extractSentencesForAudio(text);
		expect(parts).toEqual([
			'本当に実行してよろしいですか？取り消しはできません！',
			'担当者は「確認しました。」と答えました。',
			'それで終わりですか！？次へ進みます。'
		]);
	});

	it('英文は従来どおり(空白を伴う . ! ? で切り、短い文は次と合わせる)', () => {
		const text = 'Sure. Here are the points. The value is 3.14 and that is final for now.';
		expect(extractSentencesForAudio(text)).toEqual([
			'Sure. Here are the points. The value is 3.14 and that is final for now.'
		]);

		const long =
			'This first sentence is definitely long enough to stand on its own. ' +
			'The second one is also long enough to be sent as a separate request.';
		expect(extractSentencesForAudio(long)).toHaveLength(2);
	});

	it('コードブロック内の改行・句点では切らない(ブロックは読み上げから外れる)', () => {
		const text =
			'次のコードを実行してください。\n```\nprint("a。b")\nprint("c")\n```\nこれで完了です。';
		const parts = getMessageContentParts(text, 'punctuation');
		expect(parts).toEqual(['次のコードを実行してください。これで完了です。']);
	});
});
