// 寛容 JSON パーサ(#20 ステップ5)のテスト。
// 境界ケースは実キャプチャ(tool_call_long_code ほか)で観測された切断パターンに基づく:
// 文字列値の内部・エスケープシーケンスの分断・キー途中・値開始前。

import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

import { parsePartialArguments } from './partial';

describe('parsePartialArguments: 基本', () => {
	it('空文字列は「まだ何も届いていない」(空エントリ・未完)', () => {
		expect(parsePartialArguments('')).toEqual({ entries: [], done: false });
		expect(parsePartialArguments('  ')).toEqual({ entries: [], done: false });
	});

	it('object 以外で始まる入力は null(一枚岩フォールバック)', () => {
		expect(parsePartialArguments('[1, 2]')).toBeNull();
		expect(parsePartialArguments('"str"')).toBeNull();
		expect(parsePartialArguments('null')).toBeNull();
	});

	it('完全な JSON は全エントリ complete + done', () => {
		const result = parsePartialArguments('{"code": "print(1)", "timeout": 30}');
		expect(result).toEqual({
			entries: [
				{ name: 'code', value: 'print(1)', isString: true, complete: true },
				{ name: 'timeout', value: '30', isString: false, complete: true }
			],
			done: true
		});
	});

	it('空オブジェクト {} は done', () => {
		expect(parsePartialArguments('{}')).toEqual({ entries: [], done: true });
	});
});

describe('parsePartialArguments: 文字列値の途中切断', () => {
	it('値の内部で切れる(最頻: long_code は 129/129 がここ)', () => {
		const result = parsePartialArguments('{"code":"def f(');
		expect(result).toEqual({
			entries: [{ name: 'code', value: 'def f(', isString: true, complete: false }],
			done: false
		});
	});

	it('エスケープ済み改行・引用符はアンエスケープされる', () => {
		const result = parsePartialArguments('{"code":"line1\\nsay \\"hi');
		expect(result!.entries[0].value).toBe('line1\nsay "hi');
		expect(result!.entries[0].complete).toBe(false);
	});

	it('末尾の孤立バックスラッシュは保留(エスケープの分断)', () => {
		// 実キャプチャ: 'で Buzz、\' で切れて次チャンクが 'n    両'
		const partial = parsePartialArguments('{"code":"で Buzz、\\');
		expect(partial!.entries[0].value).toBe('で Buzz、'); // \ は出さない
		expect(partial!.entries[0].complete).toBe(false);

		const resumed = parsePartialArguments('{"code":"で Buzz、\\n    両');
		expect(resumed!.entries[0].value).toBe('で Buzz、\n    両');
	});

	it('完全な \\\\ エスケープ直後の切断では \\ が出る(次チャンク先頭が literal n)', () => {
		// 実キャプチャ: 'でしょう\\' + 'n'(\\ は完結したエスケープ、n はリテラル)
		const partial = parsePartialArguments('{"detail":"でしょう\\\\');
		expect(partial!.entries[0].value).toBe('でしょう\\');

		const resumed = parsePartialArguments('{"detail":"でしょう\\\\n');
		expect(resumed!.entries[0].value).toBe('でしょう\\n'); // バックスラッシュ + リテラル n
	});

	it('\\uXXXX の途中は保留、揃ったらデコード', () => {
		const partial = parsePartialArguments('{"a":"x\\u26');
		expect(partial!.entries[0].value).toBe('x');
		expect(partial!.entries[0].complete).toBe(false);

		const resumed = parsePartialArguments('{"a":"x\\u2603"');
		expect(resumed!.entries[0].value).toBe('x☃');
		expect(resumed!.entries[0].complete).toBe(true);
	});
});

describe('parsePartialArguments: キー・値の未確定領域', () => {
	it('キー途中/コロン未着/値未開始ではエントリを出さない(型未確定のちらつき防止)', () => {
		expect(parsePartialArguments('{"co')!.entries).toEqual([]);
		expect(parsePartialArguments('{"code"')!.entries).toEqual([]);
		expect(parsePartialArguments('{"code":')!.entries).toEqual([]);
		expect(parsePartialArguments('{"code": ')!.entries).toEqual([]);
	});

	it('2 個目の引数が同様に確定するまで、確定済みの 1 個目だけ出す', () => {
		const result = parsePartialArguments('{"sql":"SELECT 1;","li');
		expect(result!.entries).toEqual([
			{ name: 'sql', value: 'SELECT 1;', isString: true, complete: true }
		]);
	});
});

describe('parsePartialArguments: 非文字列値', () => {
	it('number は入力末尾で切れていたら未確定(まだ桁が伸びうる)', () => {
		const partial = parsePartialArguments('{"limit":1');
		expect(partial!.entries[0]).toEqual({
			name: 'limit',
			value: '1',
			isString: false,
			complete: false
		});

		const resumed = parsePartialArguments('{"limit":10}');
		expect(resumed!.entries[0]).toEqual({
			name: 'limit',
			value: '10',
			isString: false,
			complete: true
		});
		expect(resumed!.done).toBe(true);
	});

	it('true/false/null の途中も未確定として生テキストで返す', () => {
		expect(parsePartialArguments('{"verbose":tr')!.entries[0]).toEqual({
			name: 'verbose',
			value: 'tr',
			isString: false,
			complete: false
		});
	});

	it('ネストした object/array は閉じるまで生 JSON 断片、閉じたら complete', () => {
		const partial = parsePartialArguments('{"filters":{"city":"札');
		expect(partial!.entries[0]).toEqual({
			name: 'filters',
			value: '{"city":"札',
			isString: false,
			complete: false
		});

		const resumed = parsePartialArguments('{"filters":{"city":"札幌"}}');
		expect(resumed!.entries[0].value).toBe('{"city":"札幌"}');
		expect(resumed!.entries[0].complete).toBe(true);
		expect(resumed!.done).toBe(true);
	});
});

describe('parsePartialArguments: 実キャプチャ全プレフィックス(漸進性)', () => {
	// fixture の arguments デルタを抽出する(test_model_frame_fixtures.py と同じ規約)
	const loadArgDeltas = (name: string): string[] => {
		const sse = readFileSync(
			new URL(
				`../../../../backend/nucllei/test/fixtures/model_frames/${name}.sse`,
				import.meta.url
			),
			'utf-8'
		);
		const deltas: string[] = [];
		for (const line of sse.split('\n')) {
			if (!line.startsWith('data: ') || line.includes('[DONE]')) continue;
			const frame = JSON.parse(line.slice(6));
			for (const choice of frame.choices ?? []) {
				for (const tc of choice.delta?.tool_calls ?? []) {
					// 並列 call は index 0 のみ追う(パーサは call 単位)
					if (tc.index !== 0) continue;
					const piece = tc.function?.arguments;
					if (piece) deltas.push(piece);
				}
			}
		}
		return deltas;
	};

	for (const fixture of ['tool_call_long_code', 'tool_call_unicode_args', 'tool_call']) {
		it(`${fixture}: 全チャンク境界で throw せず、デコード値が最終値の前方一致で単調に伸びる`, () => {
			const deltas = loadArgDeltas(fixture);
			expect(deltas.length).toBeGreaterThan(0);

			const full = deltas.join('');
			const finalArgs = JSON.parse(full) as Record<string, string>;

			let acc = '';
			let prevValues: Record<string, string> = {};
			for (const piece of deltas) {
				acc += piece;
				const result = parsePartialArguments(acc);
				expect(result).not.toBeNull();
				for (const entry of result!.entries) {
					if (!entry.isString) continue;
					// 部分値は最終値の prefix(不完全エスケープの保留が効いている証明)
					expect(String(finalArgs[entry.name]).startsWith(entry.value)).toBe(true);
					// 前フレームから後退しない(単調に伸びる)
					const prev = prevValues[entry.name] ?? '';
					expect(entry.value.startsWith(prev)).toBe(true);
					prevValues[entry.name] = entry.value;
				}
			}

			// 最終状態: 全エントリ complete・done・値は JSON.parse と一致
			const finalResult = parsePartialArguments(acc)!;
			expect(finalResult.done).toBe(true);
			for (const entry of finalResult.entries) {
				expect(entry.complete).toBe(true);
				if (entry.isString) {
					expect(entry.value).toBe(finalArgs[entry.name]);
				}
			}
		});
	}
});
