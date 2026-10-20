/**
 * アイドルタイムアウトの残り時間リング(IdleTimeoutRing.svelte)の見え方の規則。
 *
 * 残り時間そのものは $lib/apis/fetch の getIdleRemaining() が出す(失効の判定と同じ値)。
 * ここは「その値をどう見せるか」だけを持つ純関数 — 画面に依存しないのでテストで固定する。
 */

/** 残りが全長のこの割合を切ったら、ホバーしなくても常に出す。 */
export const IDLE_RING_PINNED_FRACTION = 1 / 3;

/** 最後のこの時間は警告色にする(全長の 1/3 より長くはしない = 常に見えている間だけ)。 */
export const IDLE_RING_WARNING_MS = 60 * 1000;

/** リングの残量(1 = 満タン、0 = 失効)。 */
export const idleRingFraction = (remainingMs: number, timeoutMs: number): number => {
	if (!(timeoutMs > 0)) {
		return 0;
	}
	return Math.min(1, Math.max(0, remainingMs / timeoutMs));
};

/** 残りが全長の 1/3 を切った = ホバーしなくても常に出す。 */
export const isIdleRingPinned = (remainingMs: number, timeoutMs: number): boolean =>
	remainingMs < timeoutMs * IDLE_RING_PINNED_FRACTION;

/**
 * リング(と残り時間の文字)を見せるか。普段は隠し、ホバー(またはキーボードのフォーカス)で
 * 見せる。残りが 1/3 を切ったら常に見せる。
 */
export const isIdleRingVisible = ({
	remainingMs,
	timeoutMs,
	hovered
}: {
	remainingMs: number;
	timeoutMs: number;
	hovered: boolean;
}): boolean => hovered || isIdleRingPinned(remainingMs, timeoutMs);

/** 最後の 1 分(全長が短ければその 1/3)は警告色。 */
export const isIdleRingWarning = (remainingMs: number, timeoutMs: number): boolean =>
	remainingMs <= Math.min(IDLE_RING_WARNING_MS, timeoutMs * IDLE_RING_PINNED_FRACTION);

/**
 * 残り時間の表示。1 時間未満は `m:ss`(`12:34` / `0:05`)、以上は `h:mm:ss`(`3:59:59`)。
 * 秒は切り上げる — 表示が `0:00` になるのは残りがちょうど 0 になったとき(= 失効の瞬間)だけ。
 */
export const formatIdleRemaining = (remainingMs: number): string => {
	const total = Math.max(0, Math.ceil(remainingMs / 1000));
	const hours = Math.floor(total / 3600);
	const minutes = Math.floor((total % 3600) / 60);
	const seconds = total % 60;
	const ss = String(seconds).padStart(2, '0');
	if (hours > 0) {
		return `${hours}:${String(minutes).padStart(2, '0')}:${ss}`;
	}
	return `${minutes}:${ss}`;
};

/**
 * 次に表示を更新するまでの待ち(ミリ秒)。表示の秒が切り替わる瞬間に合わせる
 * (1 秒ごとの setInterval だと、表示の切り替わりと実際の残りが最大 1 秒ずれる)。
 */
export const msUntilNextIdleTick = (remainingMs: number): number => {
	const toBoundary = remainingMs % 1000;
	return toBoundary > 0 ? toBoundary : 1000;
};
