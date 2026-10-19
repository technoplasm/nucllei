import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';
import { buildWireMessages, expandAssistantToolHistory, parseStopSequences } from './chat-messages';

describe('expandAssistantToolHistory(#18-2)', () => {
	it('tool_calls ブロックが無ければ従来どおり縮約 1 メッセージ', () => {
		expect(expandAssistantToolHistory('こんにちは', undefined)).toEqual([
			{ role: 'assistant', content: 'こんにちは' }
		]);
		expect(
			expandAssistantToolHistory('こんにちは', [
				{ type: 'reasoning', content: '思考' },
				{ type: 'text', content: 'こんにちは' }
			])
		).toEqual([{ role: 'assistant', content: 'こんにちは' }]);
	});

	it('tool 往復を assistant.tool_calls + role:tool に展開する', () => {
		const blocks = [
			{ type: 'reasoning', content: '考え中' },
			{
				type: 'tool_calls',
				calls: [
					{
						id: 'functions.get_weather:0',
						type: 'function',
						function: { name: 'get_weather', arguments: '{"city": "札幌"}' },
						// 内部キーは wire に漏らさない
						render: { args: {} },
						index: 0
					}
				],
				results: [{ tool_call_id: 'functions.get_weather:0', content: '晴れ' }]
			},
			{ type: 'text', content: '札幌は晴れです。' }
		];
		expect(expandAssistantToolHistory('札幌は晴れです。', blocks)).toEqual([
			{
				role: 'assistant',
				content: '',
				tool_calls: [
					{
						id: 'functions.get_weather:0',
						type: 'function',
						function: { name: 'get_weather', arguments: '{"city": "札幌"}' }
					}
				]
			},
			{ role: 'tool', tool_call_id: 'functions.get_weather:0', content: '晴れ' },
			{ role: 'assistant', content: '札幌は晴れです。' }
		]);
	});

	it('synthetic な tool_calls(/command の合成 exchange)はスキップする', () => {
		const blocks = [
			{
				type: 'tool_calls',
				synthetic: true,
				calls: [
					{
						id: 'skill:vault:0',
						function: { name: 'load_skill', arguments: '{"skill_id": "vault"}' }
					}
				],
				results: [{ tool_call_id: 'skill:vault:0', content: '{"result": "指示", "tools": []}' }]
			},
			{
				type: 'tool_calls',
				calls: [
					{
						id: 'functions.secret_password:1',
						function: { name: 'secret_password', arguments: '{}' }
					}
				],
				results: [{ tool_call_id: 'functions.secret_password:1', content: 'pw' }]
			},
			{ type: 'text', content: 'パスワードは pw です。' }
		];
		const messages = expandAssistantToolHistory('パスワードは pw です。', blocks);
		// 合成分は消え、実 tool 往復と最終 text だけが残る
		expect(messages.map((m) => m.role)).toEqual(['assistant', 'tool', 'assistant']);
		expect(messages[0].tool_calls?.[0].function.name).toBe('secret_password');
	});

	it('複数の tool 往復と挟まれた text を順序どおり展開する', () => {
		const blocks = [
			{ type: 'text', content: '確認します。' },
			{
				type: 'tool_calls',
				calls: [{ id: 'a', function: { name: 'f1', arguments: '{}' } }],
				results: [{ tool_call_id: 'a', content: 'r1' }]
			},
			{ type: 'text', content: '次にこちら。' },
			{
				type: 'tool_calls',
				calls: [{ id: 'b', function: { name: 'f2', arguments: '{}' } }],
				results: [{ tool_call_id: 'b', content: 'r2' }]
			},
			{ type: 'text', content: '完了です。' }
		];
		const messages = expandAssistantToolHistory(
			'確認します。\n\n次にこちら。\n\n完了です。',
			blocks
		);
		expect(messages.map((m) => [m.role, m.content])).toEqual([
			['assistant', '確認します。'],
			['tool', 'r1'],
			['assistant', '次にこちら。'],
			['tool', 'r2'],
			['assistant', '完了です。']
		]);
	});

	it('結果未着の call(中断)には合成 tool result を対にする — 400 回避', () => {
		const blocks = [
			{
				type: 'tool_calls',
				calls: [
					{ id: 'a', function: { name: 'f1', arguments: '{}' } },
					{ id: 'b', function: { name: 'f2', arguments: '{}' } }
				],
				// f1 だけ結果があり f2 は中断で未着
				results: [{ tool_call_id: 'a', content: 'r1' }]
			}
		];
		const messages = expandAssistantToolHistory('', blocks);
		expect(messages.map((m) => [m.role, m.content])).toEqual([
			['assistant', ''],
			['tool', 'r1'],
			['tool', '[interrupted: this tool call produced no result]']
		]);
		expect(messages[2].tool_call_id).toBe('b');
	});

	it('arguments が壊れた call(中断で途切れ)は履歴から落とす — SGLang 400 回避', () => {
		const blocks = [
			{ type: 'text', content: '並列で呼びます' },
			{
				type: 'tool_calls',
				calls: [
					{ id: 'a', function: { name: 'echo', arguments: '{"text":"完全"}' } },
					{ id: 'b', function: { name: 'run_python', arguments: '{"code":"import da' } } // 途中切断
				],
				results: []
			}
		];
		const messages = expandAssistantToolHistory('並列で呼びます', blocks);
		expect(messages).toEqual([
			{
				role: 'assistant',
				content: '並列で呼びます',
				tool_calls: [
					{ id: 'a', type: 'function', function: { name: 'echo', arguments: '{"text":"完全"}' } }
				]
			},
			{
				role: 'tool',
				tool_call_id: 'a',
				content: '[interrupted: this tool call produced no result]'
			}
		]);
	});

	it('全 call の arguments が壊れていたらブロックごとスキップし text を合流する', () => {
		const blocks = [
			{ type: 'text', content: 'やってみます' },
			{
				type: 'tool_calls',
				calls: [{ id: 'a', function: { name: 'run_python', arguments: '{"code":"imp' } }],
				results: []
			}
		];
		expect(expandAssistantToolHistory('やってみます', blocks)).toEqual([
			{ role: 'assistant', content: 'やってみます' }
		]);
	});

	it('並列 tool call は 1 つの assistant に複数 tool_calls + 複数 tool result', () => {
		const blocks = [
			{
				type: 'tool_calls',
				calls: [
					{ id: 'a', function: { name: 'f1', arguments: '{}' } },
					{ id: 'b', function: { name: 'f2', arguments: '{}' } }
				],
				results: [
					{ tool_call_id: 'a', content: 'r1' },
					{ tool_call_id: 'b', content: 'r2' }
				]
			},
			{ type: 'text', content: '両方できました。' }
		];
		const messages = expandAssistantToolHistory('両方できました。', blocks);
		expect(messages[0].tool_calls).toHaveLength(2);
		expect(messages.map((m) => m.role)).toEqual(['assistant', 'tool', 'tool', 'assistant']);
	});
});

describe('expandAssistantToolHistory: tool 結果の画像の再送(#142)', () => {
	// バックエンド convert_content_blocks_to_messages と同じ fixture・同じ期待値
	// (test_tool_output.py test_convert_matches_shared_history_wire_fixture)
	const fixture = JSON.parse(
		readFileSync(
			new URL('../../../nucllei/test/fixtures/history_wire/context_images.json', import.meta.url),
			'utf-8'
		)
	) as { cases: { name: string; blocks: any[]; expected: unknown[] }[] };

	for (const { name, blocks, expected } of fixture.cases) {
		it(`共有 fixture: ${name}`, () => {
			expect(expandAssistantToolHistory('', blocks)).toEqual(expected);
		});
	}

	it('context_images が無い / 空なら user メッセージを挟まない', () => {
		const blocks = [
			{
				type: 'tool_calls',
				calls: [
					{ id: 'a', function: { name: 'f1', arguments: '{}' } },
					{ id: 'b', function: { name: 'f2', arguments: '{}' } }
				],
				results: [
					{ tool_call_id: 'a', content: 'r1' },
					{ tool_call_id: 'b', content: 'r2', context_images: [] }
				]
			}
		];
		expect(expandAssistantToolHistory('', blocks).map((m) => m.role)).toEqual([
			'assistant',
			'tool',
			'tool'
		]);
	});

	it('履歴から落とした call(arguments 壊れ)の result の画像は送らない', () => {
		const blocks = [
			{
				type: 'tool_calls',
				calls: [
					{ id: 'a', function: { name: 'f1', arguments: '{}' } },
					{ id: 'b', function: { name: 'f2', arguments: '{"x":' } }
				],
				results: [
					{ tool_call_id: 'a', content: 'r1', context_images: ['img-a'] },
					{ tool_call_id: 'b', content: 'r2', context_images: ['img-b'] }
				]
			}
		];
		const messages = expandAssistantToolHistory('', blocks);
		expect(messages.at(-1)).toEqual({
			role: 'user',
			content: [
				{
					type: 'text',
					text: '(以下の画像はユーザーの添付ではない — 直前の tool の結果として読み込んだ画像)'
				},
				{ type: 'image_url', image_url: { url: '/api/v1/files/img-a/content' } }
			]
		});
	});

	it('synthetic ブロックの画像は送らない(ブロックごとスキップ)', () => {
		const blocks = [
			{
				type: 'tool_calls',
				synthetic: true,
				calls: [{ id: 's', function: { name: 'load_skill', arguments: '{}' } }],
				results: [{ tool_call_id: 's', content: 'x', context_images: ['img'] }]
			},
			{ type: 'text', content: '本文' }
		];
		expect(expandAssistantToolHistory('本文', blocks)).toEqual([
			{ role: 'assistant', content: '本文' }
		]);
	});
});

describe('buildWireMessages', () => {
	it('system 文があれば先頭に注入する', () => {
		const messages = buildWireMessages([{ role: 'user', content: 'こんにちは' }], 'あなたは犬');
		expect(messages).toEqual([
			{ role: 'system', content: 'あなたは犬' },
			{ role: 'user', content: 'こんにちは' }
		]);
	});

	it('system 文が無ければ注入しない(null / undefined / 空文字)', () => {
		for (const system of [null, undefined, '']) {
			const messages = buildWireMessages([{ role: 'user', content: 'hi' }], system);
			expect(messages).toEqual([{ role: 'user', content: 'hi' }]);
		}
	});

	it('user の画像 file は image_url パートに展開し、url をそのまま通す', () => {
		const messages = buildWireMessages(
			[
				{
					role: 'user',
					content: 'これ見て',
					files: [
						{ type: 'image', url: '/api/v1/files/abc/content' },
						{ type: 'file', url: '/api/v1/files/doc' },
						{ type: 'image', url: 'data:image/png;base64,xyz' }
					]
				}
			],
			null
		);
		expect(messages).toEqual([
			{
				role: 'user',
				content: [
					{ type: 'text', text: 'これ見て' },
					{ type: 'image_url', image_url: { url: '/api/v1/files/abc/content' } },
					{ type: 'image_url', image_url: { url: 'data:image/png;base64,xyz' } }
				]
			}
		]);
	});

	it('画像だけの発話(本文が空・空白のみ)には空の text パートを付けない', () => {
		for (const content of ['', '  \n']) {
			const messages = buildWireMessages(
				[{ role: 'user', content, files: [{ type: 'image', url: '/api/v1/files/abc/content' }] }],
				null
			);
			expect(messages).toEqual([
				{
					role: 'user',
					content: [{ type: 'image_url', image_url: { url: '/api/v1/files/abc/content' } }]
				}
			]);
		}
	});

	it('画像なし user はプレーン {role, content} のまま', () => {
		const messages = buildWireMessages(
			[{ role: 'user', content: '文書だけ', files: [{ type: 'file', url: '/x' }] }],
			null
		);
		expect(messages).toEqual([{ role: 'user', content: '文書だけ' }]);
	});

	it('assistant の tool 往復は wire 形(assistant+tool_calls → tool)に復元する', () => {
		const messages = buildWireMessages(
			[
				{ role: 'user', content: '実行して' },
				{
					role: 'assistant',
					content: '結果です',
					content_blocks: [
						{
							type: 'tool_calls',
							calls: [{ id: 'c1', function: { name: 'run_python', arguments: '{}' } }],
							results: [{ tool_call_id: 'c1', content: '42' }]
						},
						{ type: 'text', content: '結果です' }
					]
				}
			],
			null
		);
		expect(messages).toEqual([
			{ role: 'user', content: '実行して' },
			{
				role: 'assistant',
				content: '',
				tool_calls: [
					{ id: 'c1', type: 'function', function: { name: 'run_python', arguments: '{}' } }
				]
			},
			{ role: 'tool', tool_call_id: 'c1', content: '42' },
			{ role: 'assistant', content: '結果です' }
		]);
	});

	it('tool 結果の画像は次ターンの履歴に user(image_url) として残る(#142)', () => {
		const messages = buildWireMessages(
			[
				{ role: 'user', content: '図を見て' },
				{
					role: 'assistant',
					content: '見ました',
					content_blocks: [
						{
							type: 'tool_calls',
							calls: [{ id: 'c1', function: { name: 'view_image', arguments: '{}' } }],
							results: [{ tool_call_id: 'c1', content: '提示しました。', context_images: ['abc'] }]
						},
						{ type: 'text', content: '見ました' }
					]
				},
				{ role: 'user', content: '何が写っていた?' }
			],
			null
		);
		expect(messages.map((m) => m.role)).toEqual([
			'user',
			'assistant',
			'tool',
			'user',
			'assistant',
			'user'
		]);
		expect(messages[3]).toEqual({
			role: 'user',
			content: [
				{
					type: 'text',
					text: '(以下の画像はユーザーの添付ではない — 直前の tool の結果として読み込んだ画像)'
				},
				{ type: 'image_url', image_url: { url: '/api/v1/files/abc/content' } }
			]
		});
	});

	it('空 content の assistant は落とす・user は空でも残す', () => {
		const messages = buildWireMessages(
			[
				{ role: 'user', content: '' },
				{ role: 'assistant', content: '   ' }
			],
			null
		);
		expect(messages).toEqual([{ role: 'user', content: '' }]);
	});
});

describe('parseStopSequences', () => {
	it('未指定(undefined / null / 空文字)は undefined', () => {
		expect(parseStopSequences(undefined)).toBeUndefined();
		expect(parseStopSequences(null)).toBeUndefined();
		expect(parseStopSequences('')).toBeUndefined();
	});

	it('カンマ区切り文字列を trim して配列化する', () => {
		expect(parseStopSequences('END, STOP')).toEqual(['END', 'STOP']);
	});

	it('エスケープ表記("\\n" 等)を実文字へ戻す', () => {
		expect(parseStopSequences('\\n,###')).toEqual(['\n', '###']);
	});

	it('配列(settings.params.stop 由来)も受ける — upstream では TypeError だった経路', () => {
		expect(parseStopSequences(['END', '\\n'])).toEqual(['END', '\n']);
	});
});
