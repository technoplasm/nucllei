import { describe, it, expect } from 'vitest';
import { segmentMarkdown, closeUnclosedFences, StreamingSegmenter } from './segmenter';

// ────────────────────────────────────────────────────────────────────────
// 追記単調性チェックの共通ヘルパ
// ────────────────────────────────────────────────────────────────────────

function isElementPrefix(a: string[], b: string[]): boolean {
	if (a.length > b.length) return false;
	return a.every((v, i) => v === b[i]);
}

/** 全プレフィックス(1 文字ずつ)で stable が単調に伸びることを検証。 */
function assertMonotonic(doc: string): void {
	let prev: string[] = [];
	// Array.from で code point 単位に分割(結合絵文字などの分断を避ける)
	const chars = Array.from(doc);
	let acc = '';
	for (const ch of chars) {
		acc += ch;
		const cur = segmentMarkdown(acc).stable;
		expect(isElementPrefix(prev, cur)).toBe(true);
		prev = cur;
	}
	// 最終形は文書全体と一致
	expect(segmentMarkdown(doc).stable).toEqual(prev);
}

/** 決定的な PRNG(mulberry32)。 */
function rng(seed: number): () => number {
	let a = seed >>> 0;
	return () => {
		a |= 0;
		a = (a + 0x6d2b79f5) | 0;
		let t = Math.imul(a ^ (a >>> 15), 1 | a);
		t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
		return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
	};
}

/** ランダムなチャンク分割で流し込んでも単調性が保たれることを検証。 */
function assertMonotonicRandomChunks(doc: string, seed: number): void {
	const rand = rng(seed);
	const chars = Array.from(doc);
	let acc = '';
	let prev: string[] = [];
	let idx = 0;
	while (idx < chars.length) {
		const step = 1 + Math.floor(rand() * 7);
		acc += chars.slice(idx, idx + step).join('');
		idx += step;
		const cur = segmentMarkdown(acc).stable;
		expect(isElementPrefix(prev, cur)).toBe(true);
		prev = cur;
	}
	expect(segmentMarkdown(doc).stable).toEqual(prev);
}

// ────────────────────────────────────────────────────────────────────────
// 実サンプル(render_probe.py の _sample_* 相当を TS で再現)
// ────────────────────────────────────────────────────────────────────────

const SAMPLE_TABLE = `## 表のレンダリング確認

小さい表:

| 項目 | 値 |
| --- | --- |
| pH | 7.35 |
| PaCO2 | 45 mmHg |
| HCO3⁻ | 24 mEq/L |

大きい表(整列混在):

| # | 薬剤 | 用量 | 経路 | 頻度 | 備考(長め) |
| ---: | :--- | ---: | :---: | :--- | :--- |
| 1 | アセトアミノフェン | 500mg | PO | q6h | 肝機能に注意 |
`;

const SAMPLE_CODE = `## コードフェンスのレンダリング確認

インラインコード: \`printf("%d\\n", x);\` と \`<div class="x">\`。

\`\`\`python
# 日本語コメント: 特殊文字 <>&"' を含む
def greet(name: str) -> str:
    return f"こんにちは、{name}さん！"
\`\`\`

\`\`\`javascript
// バッククォート内テンプレート
const msg = \`合計: \${1 + 2} 円\`;
console.log(msg);
\`\`\`
`;

const SAMPLE_LONG = `# 長文サンプル(見出し・リスト・引用)

## はじめに

これは長文レンダリングの確認用テキストです。段落が複数あり、見出し階層とリスト・引用が混在します。

### 箇条書き

- 第1項目
- 第2項目
  - ネストした項目 A
  - ネストした項目 B
- 第3項目

### 番号付きリスト

1. 手順その1
2. 手順その2
3. 手順その3

### 引用

> これは引用ブロックです。
> 複数行にわたる引用も確認します。
>
> — 出典(架空)

### まとめ

段落・強調(**太字** と *斜体*)・[リンク風テキスト](https://example.invalid) も含みます。
`;

const SAMPLE_EDGE = `# エッジケース

未クローズのコードフェンス(以降が全部コード扱いになるか確認):

\`\`\`python
x = 1  # このフェンスは閉じない
y = 2

HTML タグ混入(サニタイズ確認): <script>alert('xss')</script> と <b>bold?</b>。

reasoning 誤検出確認: <think>これは思考タグ風の文字列</think>

絵文字: 🩺💊🧪🚑✅❌ と結合絵文字 👨‍⚕️👩‍⚕️。

超長い1行: ${'あ'.repeat(200)} END

特殊文字: <>&"'\` \\ 	(タブ) と全角空白　です。
`;

const SAMPLE_MIXED = `# 全部入りサンプル

${SAMPLE_LONG}

---

${SAMPLE_TABLE}

---

${SAMPLE_CODE}
`;

// backend/open_webui/test/fixtures/model_frames/reasoning.json の content(実キャプチャ)
const FIXTURE_REASONING_CONTENT = `1. 農夫、山羊を運ぶ
2. 農夫、戻る
3. 農夫、狼を運ぶ
4. 農夫、山羊を連れて戻る
5. 農夫、キャベツを運ぶ
6. 農夫、戻る
7. 農夫、山羊を運ぶ

（狼とキャベツの順序を入れ替えても可）`;

// backend/open_webui/test/fixtures/model_frames/reasoning.json の reasoning_content 抜粋
const FIXTURE_REASONING_THINK = `ユーザーは古典的な「農夫と狼と山羊とキャベツ」の川渡りパズルの最短手順を求めています。簡潔に手順だけを答える必要があります。

このパズルの制約：
- 農夫は狼、山羊、キャベツを川の向こう岸に運ぶ必要がある
- 舟は農夫しか漕げず、農夫と一緒に1つだけ運べる
- 狼と山羊を残すと、狼が山羊を食べる

最短手順（7手）：
1. 農夫が山羊を運ぶ
2. 農夫が戻る`;

const ALL_SAMPLES: Record<string, string> = {
	table: SAMPLE_TABLE,
	code: SAMPLE_CODE,
	long: SAMPLE_LONG,
	edge: SAMPLE_EDGE,
	mixed: SAMPLE_MIXED,
	fixtureContent: FIXTURE_REASONING_CONTENT,
	fixtureThink: FIXTURE_REASONING_THINK
};

// ────────────────────────────────────────────────────────────────────────
// 固定ケース:各文法の「途中で切ると壊れる位置」での挙動
// ────────────────────────────────────────────────────────────────────────

describe('segmentMarkdown — 固定ケース', () => {
	it('setext: 段落は次行が --- になりうる間は確定しない', () => {
		// 段落 + 単一改行だけでは確定しない(次に --- が来て h2 に化けうる)
		expect(segmentMarkdown('見出し候補\n')).toEqual({ stable: [], tail: '見出し候補\n' });
		// 実際に setext 下線が来ても、空行が無いので依然 tail のまま
		const setext = segmentMarkdown('見出し候補\n---\n');
		expect(setext.stable).toEqual([]);
		// 空行で閉じれば確定(以後 --- は遡及できない)
		const sealed = segmentMarkdown('段落です。\n\n');
		expect(sealed.stable).toEqual(['段落です。']);
	});

	it('table: ヘッダ行は区切り行が来るまで(=空行が来るまで)確定しない', () => {
		// ヘッダ候補のみ → 未確定
		expect(segmentMarkdown('| a | b |\n').stable).toEqual([]);
		// 区切り行まで来ても空行前なら tail
		expect(segmentMarkdown('| a | b |\n| --- | --- |\n').stable).toEqual([]);
		// 空行で閉じた表は 1 ブロックとして確定
		const t = segmentMarkdown('| a | b |\n| --- | --- |\n| 1 | 2 |\n\n');
		expect(t.stable).toEqual(['| a | b |\n| --- | --- |\n| 1 | 2 |']);
	});

	it('fence: 閉じフェンスが完全な行で現れるまで確定しない', () => {
		const open = segmentMarkdown('```py\nx = 1\n');
		expect(open.stable).toEqual([]);
		expect(open.tail).toBe('```py\nx = 1\n');
		// 閉じフェンスがまだ未完(改行前)→ 未確定
		expect(segmentMarkdown('```py\nx = 1\n```').stable).toEqual([]);
		// 閉じフェンス行が完結 → 確定
		const closed = segmentMarkdown('```py\nx = 1\n```\n');
		expect(closed.stable).toEqual(['```py\nx = 1\n```']);
	});

	it('fence: フェンスは空行を跨ぐ(空行で割れない)', () => {
		const doc = '```\na\n\nb\n```\n';
		expect(segmentMarkdown(doc).stable).toEqual(['```\na\n\nb\n```']);
	});

	it('HTML ブロック(コメント)は空行を跨ぎ、終端 --> まで確定しない', () => {
		const open = segmentMarkdown('<!-- start\n\nまだコメント内\n');
		expect(open.stable).toEqual([]);
		const closed = segmentMarkdown('<!-- start\n\nまだコメント内 -->\n');
		expect(closed.stable).toEqual(['<!-- start\n\nまだコメント内 -->']);
	});

	it('#48 回帰: <details> は空行を跨ぎ </details> まで 1 ブロック', () => {
		// reasoning 本文に空行があっても details を分断しない(分断すると閉じ
		// </details> が孤立ブロックになりリテラル表示される)。
		const src =
			'<details type="reasoning" done="true" duration="2">\n<summary>Thought for 2 seconds</summary>\n最初の思考。\n\n二段落目。\n</details>\n\n答えの本文。';
		const { stable } = segmentMarkdown(src);
		// details 全体が単一の確定ブロックになる
		expect(stable[0]).toBe(
			'<details type="reasoning" done="true" duration="2">\n<summary>Thought for 2 seconds</summary>\n最初の思考。\n\n二段落目。\n</details>'
		);
		// 孤立した </details> ブロックが生まれない
		expect(stable.some((b) => b.trim() === '</details>')).toBe(false);
	});

	it('#48 回帰: 閉じ未着の <details> は確定しない(空行跨ぎで保留)', () => {
		const { stable } = segmentMarkdown(
			'<details type="reasoning" done="false">\n<summary>Thinking…</summary>\n途中\n\nまだ続く\n'
		);
		expect(stable).toEqual([]);
	});

	it('リスト継続: リストは終端が別ブロックで証明されるまで確定しない', () => {
		// 末尾がリスト → 空行があっても保留(次項目 / インデント継続がありうる)
		expect(segmentMarkdown('- a\n- b\n\n').stable).toEqual([]);
		// loose list 内の空行では割れない
		expect(segmentMarkdown('- a\n\n- b\n\n').stable).toEqual([]);
		// 非インデントの別段落が来て初めてリストが確定
		const ended = segmentMarkdown('- a\n- b\n\n次の段落\n\n');
		expect(ended.stable).toEqual(['- a\n- b', '次の段落']);
	});

	it('リスト継続: 空行後のインデント行はリストに吸われる(確定しない)', () => {
		const doc = '- a\n\n  継続段落\n';
		expect(segmentMarkdown(doc).stable).toEqual([]);
	});

	it('参照リンク定義は特別扱いせず通常段落として確定する(意図的判断)', () => {
		const doc = '[foo]: https://example.invalid\n\n';
		expect(segmentMarkdown(doc).stable).toEqual(['[foo]: https://example.invalid']);
	});

	it('空入力・空行のみ', () => {
		expect(segmentMarkdown('')).toEqual({ stable: [], tail: '' });
		expect(segmentMarkdown('\n\n\n')).toEqual({ stable: [], tail: '\n\n\n' });
	});
});

// ────────────────────────────────────────────────────────────────────────
// closeUnclosedFences
// ────────────────────────────────────────────────────────────────────────

describe('closeUnclosedFences', () => {
	it('未クローズフェンスを仮想的に閉じる', () => {
		expect(closeUnclosedFences('```py\nx = 1\n')).toBe('```py\nx = 1\n```');
		expect(closeUnclosedFences('```py\nx = 1')).toBe('```py\nx = 1\n```');
	});

	it('既に閉じているフェンスはそのまま', () => {
		const s = '```\nx\n```\n';
		expect(closeUnclosedFences(s)).toBe(s);
	});

	it('フェンス無しはそのまま', () => {
		expect(closeUnclosedFences('ただの段落')).toBe('ただの段落');
	});

	it('入れ子規則: ~~~ フェンス内の ``` は content 扱い、~~~ で閉じる', () => {
		// ~~~ が開いていて内部に ``` がある。閉じるのは ~~~。
		const tail = '~~~\n```\nコード\n';
		expect(closeUnclosedFences(tail)).toBe('~~~\n```\nコード\n~~~');
	});

	it('同種でも長さが足りない行では閉じない', () => {
		// 開き ```` (4個)は ``` (3個)では閉じない → まだ open
		expect(closeUnclosedFences('````\nx\n```\n')).toBe('````\nx\n```\n````');
	});
});

// ────────────────────────────────────────────────────────────────────────
// StreamingSegmenter ラッパは純関数と一致する
// ────────────────────────────────────────────────────────────────────────

describe('StreamingSegmenter', () => {
	it('push 累積結果は segmentMarkdown と一致し、newlyStable は増分のみ', () => {
		const doc = SAMPLE_LONG;
		const seg = new StreamingSegmenter();
		const chars = Array.from(doc);
		const collected: string[] = [];
		for (const ch of chars) {
			const r = seg.push(ch);
			collected.push(...r.newlyStable);
			// 各時点で純関数と一致
			expect(r.stable).toEqual(segmentMarkdown(seg.value).stable);
		}
		// 増分の総和 = 最終 stable
		expect(collected).toEqual(segmentMarkdown(doc).stable);
	});
});

// ────────────────────────────────────────────────────────────────────────
// プロパティテスト:追記単調性(1 文字ずつ / ランダム分割)
// ────────────────────────────────────────────────────────────────────────

describe('プロパティ: 追記単調性(1 文字ずつ)', () => {
	for (const [name, doc] of Object.entries(ALL_SAMPLES)) {
		it(`monotonic char-by-char: ${name}`, () => {
			assertMonotonic(doc);
		});
	}
});

describe('プロパティ: 追記単調性(ランダム分割 × 複数 seed)', () => {
	const seeds = [1, 42, 1337, 20260705, 99999];
	for (const [name, doc] of Object.entries(ALL_SAMPLES)) {
		for (const seed of seeds) {
			it(`monotonic random(${seed}): ${name}`, () => {
				assertMonotonicRandomChunks(doc, seed);
			});
		}
	}
});

// 遡及文法を凝縮した合成ドキュメントでも単調性を保つ
describe('プロパティ: 遡及文法てんこ盛りドキュメント', () => {
	const adversarial = [
		'段落A',
		'setext候補',
		'---',
		'',
		'| h1 | h2 |',
		'| --- | --- |',
		'| 1 | 2 |',
		'',
		'```ts',
		'const x = 1;',
		'',
		'const y = 2;',
		'```',
		'',
		'<!-- comment',
		'',
		'still -->',
		'',
		'- item1',
		'- item2',
		'',
		'  lazy continuation',
		'',
		'最終段落',
		''
	].join('\n');

	it('char-by-char monotonic', () => {
		assertMonotonic(adversarial);
	});
	for (const seed of [7, 13, 2024]) {
		it(`random(${seed}) monotonic`, () => {
			assertMonotonicRandomChunks(adversarial, seed);
		});
	}
});
