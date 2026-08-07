/**
 * 1 秒ごとに進む共有クロック(#100)。
 *
 * 実行中の経過時間表示(思考中・tool 実行中・追撃待ち)は「動いていること」が
 * 伝わればよく、精度は要らない。ブロックごとに setInterval を張ると本数分だけ
 * タイマが増えるので、購読者がいる間だけ 1 本走らせて共有する。
 *
 * 使い方(コンポーネント側):
 *
 * ```ts
 * $effect(() => {
 *   if (done) return;
 *   return subscribeTick(); // 戻り値の解除関数を $effect が呼ぶ
 * });
 * const elapsed = $derived(done ? 0 : (tickNow() - startedAt) / 1000);
 * ```
 */

let now = $state(Date.now());
let subscribers = 0;
let timer: ReturnType<typeof setInterval> | null = null;

/** 現在時刻(1 秒ごとに更新される reactive な読み取り)。 */
export const tickNow = () => now;

/** 共有タイマを購読する。戻り値を呼ぶと解除(最後の 1 人が抜けたら停止)。 */
export const subscribeTick = (): (() => void) => {
	subscribers += 1;
	if (timer === null) {
		now = Date.now();
		timer = setInterval(() => {
			now = Date.now();
		}, 1000);
	}
	return () => {
		subscribers -= 1;
		if (subscribers <= 0 && timer !== null) {
			clearInterval(timer);
			timer = null;
			subscribers = 0;
		}
	};
};
