import { describe, expect, it } from 'vitest';
import { expandAssistantToolHistory } from './chat-messages';

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
						function: { name: 'load_skill', arguments: '{"skill_command": "vault"}' }
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
