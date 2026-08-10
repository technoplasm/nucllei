import { describe, it, expect } from 'vitest';

import { collectChatFiles } from './chat-files';
import type { ChatFile, ChatHistory } from '$lib/types/chat';

const historyWith = (files: Record<string, ChatFile[]>): ChatHistory =>
	({
		currentId: null,
		messages: Object.fromEntries(Object.entries(files).map(([id, f]) => [id, { id, files: f }]))
	}) as unknown as ChatHistory;

describe('collectChatFiles', () => {
	it('chat レベルの添付(type file)を拾う', () => {
		const items = collectChatFiles([{ type: 'file', id: 'f1', name: 'a.pdf', size: 10 }], null);
		expect(items).toEqual([{ id: 'f1', type: 'file', name: 'a.pdf', size: 10 }]);
	});

	it('メッセージの画像(id 付き)を拾い、id 無しの旧 data URL 画像は無視する', () => {
		const items = collectChatFiles(
			[],
			historyWith({
				m1: [
					{ type: 'image', id: 'img1', name: 'p.png' },
					{ type: 'image', url: 'data:image/png;base64,xx' } // id 無し
				]
			})
		);
		expect(items).toEqual([{ id: 'img1', type: 'image', name: 'p.png', size: null }]);
	});

	it('tool 生成ファイル(メッセージ files の type file)を拾う — run_python の成果物', () => {
		// sandbox の _attachment_dict と同形
		const generated: ChatFile = {
			type: 'file',
			id: 'gen1',
			name: 'result.csv',
			status: 'uploaded',
			file: {
				filename: 'result.csv',
				meta: { name: 'result.csv', content_type: 'text/csv', size: 42 }
			}
		};
		const items = collectChatFiles([], historyWith({ m1: [generated] }));
		expect(items).toEqual([{ id: 'gen1', type: 'file', name: 'result.csv', size: 42 }]);
	});

	it('tool 生成の画像は content_type からサムネイル種別(image)にする', () => {
		const generated: ChatFile = {
			type: 'file',
			id: 'gen2',
			name: 'plot.png',
			file: { meta: { name: 'plot.png', content_type: 'image/png', size: 7 } }
		};
		const items = collectChatFiles([], historyWith({ m1: [generated] }));
		expect(items[0].type).toBe('image');
	});

	it('chat レベルとメッセージで同じ id は 1 件に dedupe する', () => {
		const items = collectChatFiles(
			[{ type: 'file', id: 'dup', name: 'x.txt' }],
			historyWith({ m1: [{ type: 'file', id: 'dup', name: 'x.txt' }] })
		);
		expect(items).toHaveLength(1);
	});

	it('未知の type と空入力は無視する', () => {
		expect(collectChatFiles([{ type: 'collection', id: 'kb1' } as ChatFile], null)).toEqual([]);
		expect(collectChatFiles(null, null)).toEqual([]);
	});

	const UUID = '11111111-2222-3333-4444-555555555555';
	const historyWithContent = (content: string): ChatHistory =>
		({
			currentId: 'm1',
			messages: { m1: { id: 'm1', content, files: [] } }
		}) as unknown as ChatHistory;

	it('本文のインライン画像参照(添付永続化を逃したプロット)を拾う', () => {
		const items = collectChatFiles(
			[],
			historyWithContent(`結果です\n![plot.png](/api/v1/files/${UUID}/content)`)
		);
		expect(items).toEqual([{ id: UUID, type: 'image', name: 'plot.png', size: null }]);
	});

	it('本文のインラインリンク参照は file として拾う', () => {
		const items = collectChatFiles(
			[],
			historyWithContent(`[result.csv](/api/v1/files/${UUID}/content) をどうぞ`)
		);
		expect(items).toEqual([{ id: UUID, type: 'file', name: 'result.csv', size: null }]);
	});

	it('インライン参照と files 配列の同一 id は files 側のメタデータが勝つ', () => {
		const history = {
			currentId: 'm1',
			messages: {
				m1: {
					id: 'm1',
					content: `![p](/api/v1/files/${UUID}/content)`,
					files: [{ type: 'file', id: UUID, name: 'plot.png', size: 99 }]
				}
			}
		} as unknown as ChatHistory;
		const items = collectChatFiles([], history);
		expect(items).toEqual([{ id: UUID, type: 'file', name: 'plot.png', size: 99 }]);
	});

	it('パスを伴わない裸の UUID は拾わない(tool 結果の任意 id で誤爆しない)', () => {
		const items = collectChatFiles([], historyWithContent(`file_id=${UUID} で登録済み`));
		expect(items).toEqual([]);
	});
});
