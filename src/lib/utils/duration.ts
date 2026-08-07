/**
 * 所要時間(秒)の表示丸め(#100)。
 *
 * backend は reasoning / tool とも小数で持つ(`int()` 切り捨てだと 1 秒未満の思考が
 * 全部「0秒」になっていた)。丸めは表示側の責務で、reasoning ブロックと tool
 * ブロックで同じ規則を使う。
 */

/**
 * 10 秒未満は 0.1 秒刻み(短い tool どうしの差が潰れない)、以降は整数。
 * 非数・負値は 0 に落とす。
 */
export const formatDurationSeconds = (seconds: unknown): string => {
	if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) {
		return '0';
	}
	return seconds < 10 ? seconds.toFixed(1) : Math.round(seconds).toString();
};
