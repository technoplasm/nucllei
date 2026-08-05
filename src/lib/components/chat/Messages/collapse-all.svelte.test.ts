import { describe, it, expect } from 'vitest';
import { flushSync } from 'svelte';
import { collapseAllSignal, requestCollapseAll } from './collapse-all.svelte';

/**
 * ReasoningBlock / ToolCallBlock の追従ロジックと同形の購読者。
 * 別メッセージに属するブロックでも同じ合図を見ていることを、複数インスタンスで確かめる。
 */
function makeBlock(initialOpen: boolean) {
	let open = $state(initialOpen);
	// 初期値キャプチャ(seq 比較の lastState ガード)— 実装と同じ
	let lastSeq = collapseAllSignal.seq;
	$effect(() => {
		if (collapseAllSignal.seq !== lastSeq) {
			lastSeq = collapseAllSignal.seq;
			open = collapseAllSignal.open;
		}
	});
	return {
		get open() {
			return open;
		},
		set open(v: boolean) {
			open = v;
		}
	};
}

describe('「すべて閉じる」の合図', () => {
	it('別メッセージのブロックも含めて開いているものが全部閉じる', () => {
		const cleanup = $effect.root(() => {
			// 3 通のメッセージにまたがるブロック相当(2 つ開、1 つ閉)
			const a = makeBlock(true);
			const b = makeBlock(true);
			const c = makeBlock(false);
			flushSync();

			requestCollapseAll(false);
			flushSync();

			expect(a.open).toBe(false);
			expect(b.open).toBe(false);
			expect(c.open).toBe(false);
		});
		cleanup();
	});

	it('一括操作のあとも個別の手動開閉は殺されない', () => {
		const cleanup = $effect.root(() => {
			const a = makeBlock(true);
			const b = makeBlock(true);
			flushSync();

			requestCollapseAll(false);
			flushSync();

			// 片方だけ開き直す — 合図は seq が進んだときしか効かないので上書きされない
			a.open = true;
			flushSync();

			expect(a.open).toBe(true);
			expect(b.open).toBe(false);
		});
		cleanup();
	});

	it('あとから現れたブロックは過去の合図に巻き戻されない', () => {
		const cleanup = $effect.root(() => {
			requestCollapseAll(false);
			flushSync();

			// 合図のあとにマウントされたブロック(ストリーム中に届いた新ブロック相当)
			const late = makeBlock(true);
			flushSync();

			expect(late.open).toBe(true);
		});
		cleanup();
	});

	it('seq は押すたびに進む(同じ open でも再通知できる)', () => {
		const before = collapseAllSignal.seq;
		requestCollapseAll(false);
		requestCollapseAll(false);
		expect(collapseAllSignal.seq).toBe(before + 2);
	});
});
