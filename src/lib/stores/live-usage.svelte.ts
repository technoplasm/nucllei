/**
 * 生成中のトークン使用量(#102)。
 *
 * SSE の `chat:usage`(~0.5 秒間隔、当該モデル呼び出しの累積値)を受ける揮発 state。
 * tool-progress.svelte.ts と同じ規律で **message オブジェクトの外**に置く — message に
 * 生やすと `saveChatHandler` が history を丸ごと DB に書くため「揮発」の規律が破れる。
 *
 * - live エントリ(messageId キー): ターン進行中インジケータ(TurnIndicator)と
 *   floating ゲージ(UsageChip)の生成中表示。`chat:completion` の done で破棄。
 *   tool ループでは呼び出しごとに completion_tokens が小さい値へ巻き戻る —
 *   これを新セグメントの開始と解釈し、それまでの completion をターン累計に積む。
 * - チャットレベル state: floating ゲージの確定値(baseline — `chat:completion` の
 *   usage / `Chat.meta.usage` から)と、直近ターンの平均 tok/s(lastTurnRate)。
 *   チャット切替(loadChat / initNewChat)で resetChatUsage() する。
 */

export type UsagePayload = {
	prompt_tokens?: number;
	completion_tokens?: number;
	total_tokens?: number;
	reasoning_tokens?: number;
	context_size?: number;
};

export type LiveUsage = {
	prompt_tokens: number;
	completion_tokens: number;
	total_tokens: number;
	reasoning_tokens?: number;
	context_size?: number;
	/** 最初のサンプルを観測した時刻(クライアント時計)。経過時間・平均レートの起点。 */
	startedAt: number;
	/** 直近サンプルの観測時刻。瞬間レートの分母と「最新エントリ」判定に使う。 */
	sampledAt: number;
	/** 巻き戻り(新セグメント)までに積んだ completion のターン累計(現セグメント分は含まない)。 */
	segmentBase: number;
	/** EMA 平滑した tok/s。サンプル 2 件目から値が付く。 */
	rate: number | null;
};

export type ChatUsageBaseline = {
	prompt_tokens?: number;
	completion_tokens?: number;
	total_tokens?: number;
	context_size?: number;
};

/** 瞬間レートの EMA 係数。0.5 秒間隔のサンプルで数秒スケールに平滑化する。 */
const EMA_ALPHA = 0.3;

const entries = $state<Record<string, LiveUsage>>({});

const chatUsage = $state<{ baseline: ChatUsageBaseline | null; lastTurnRate: number | null }>({
	baseline: null,
	lastTurnRate: null
});

const asNumber = (value: unknown): number | undefined =>
	typeof value === 'number' && Number.isFinite(value) ? value : undefined;

/** SSE の `chat:usage`(モデル呼び出しの累積値)を適用する。 */
export const setLiveUsage = (messageId: string, payload: UsagePayload | null | undefined) => {
	if (!messageId || payload == null) {
		return;
	}
	const now = Date.now();
	const completion = asNumber(payload.completion_tokens) ?? 0;
	const previous = entries[messageId];

	if (!previous) {
		entries[messageId] = {
			prompt_tokens: asNumber(payload.prompt_tokens) ?? 0,
			completion_tokens: completion,
			total_tokens: asNumber(payload.total_tokens) ?? 0,
			reasoning_tokens: asNumber(payload.reasoning_tokens),
			context_size: asNumber(payload.context_size),
			startedAt: now,
			sampledAt: now,
			segmentBase: 0,
			rate: null
		};
		return;
	}

	let { segmentBase, rate } = previous;
	if (completion < previous.completion_tokens) {
		// 新セグメント(tool ループの次のモデル呼び出し)— それまでの completion を
		// ターン累計へ積み、瞬間レートの基準をリセットする(EMA 値は維持)
		segmentBase += previous.completion_tokens;
	} else {
		const dtSeconds = (now - previous.sampledAt) / 1000;
		if (dtSeconds > 0) {
			const instant = (completion - previous.completion_tokens) / dtSeconds;
			rate = rate === null ? instant : rate + EMA_ALPHA * (instant - rate);
		}
	}

	entries[messageId] = {
		prompt_tokens: asNumber(payload.prompt_tokens) ?? previous.prompt_tokens,
		completion_tokens: completion,
		total_tokens: asNumber(payload.total_tokens) ?? previous.total_tokens,
		reasoning_tokens: asNumber(payload.reasoning_tokens) ?? previous.reasoning_tokens,
		context_size: asNumber(payload.context_size) ?? previous.context_size,
		startedAt: previous.startedAt,
		sampledAt: now,
		segmentBase,
		rate
	};
};

export const getLiveUsage = (messageId: string): LiveUsage | null => entries[messageId] ?? null;

/** 最新(直近サンプル)の生きているエントリ 1 件 — floating ゲージ用。 */
export const getActiveLiveUsage = (): LiveUsage | null => {
	let latest: LiveUsage | null = null;
	for (const entry of Object.values(entries)) {
		if (!latest || entry.sampledAt > latest.sampledAt) {
			latest = entry;
		}
	}
	return latest;
};

/**
 * ターン終了(done)時の破棄。ターン累計 completion / 実測経過秒を
 * チャットレベルの平均 tok/s(lastTurnRate)として残す。
 */
export const clearLiveUsage = (messageId: string) => {
	if (!messageId) {
		return;
	}
	const entry = entries[messageId];
	if (!entry) {
		return;
	}
	delete entries[messageId];

	const turnCompletion = entry.segmentBase + entry.completion_tokens;
	const elapsedSeconds = (entry.sampledAt - entry.startedAt) / 1000;
	if (turnCompletion > 0 && elapsedSeconds > 0) {
		chatUsage.lastTurnRate = turnCompletion / elapsedSeconds;
	}
};

/**
 * floating ゲージの確定値。`chat:completion` の usage、またはチャット読込時の
 * `Chat.meta.usage` から。数値フィールドが 1 つも無い形は無視する(据え置き)。
 */
export const setChatUsageBaseline = (usage: Record<string, unknown> | null | undefined) => {
	if (usage == null) {
		chatUsage.baseline = null;
		return;
	}
	const baseline: ChatUsageBaseline = {
		prompt_tokens: asNumber(usage.prompt_tokens),
		completion_tokens: asNumber(usage.completion_tokens),
		total_tokens: asNumber(usage.total_tokens),
		context_size: asNumber(usage.context_size)
	};
	if (
		baseline.prompt_tokens === undefined &&
		baseline.completion_tokens === undefined &&
		baseline.total_tokens === undefined
	) {
		return;
	}
	chatUsage.baseline = baseline;
};

export const getChatUsageBaseline = (): ChatUsageBaseline | null => chatUsage.baseline;

export const getLastTurnRate = (): number | null => chatUsage.lastTurnRate;

/**
 * チャット切替時のリセット。baseline / lastTurnRate に加えて live エントリも捨てる —
 * 前のチャットのエントリが残ると floating ゲージ(getActiveLiveUsage)が別チャットの
 * 値を出す。生成中のチャットへ戻れば backend が再アタッチで最新 1 件を再送してくる。
 */
export const resetChatUsage = () => {
	for (const key of Object.keys(entries)) {
		delete entries[key];
	}
	chatUsage.baseline = null;
	chatUsage.lastTurnRate = null;
};

/**
 * トークン数の表示整形(1024 基数)。llens token_meter の `_fmt_num` と同じ見え方:
 * 262144 → "256.0k"、1023 → "1023"、1572864 → "1.5M"。
 */
export const formatTokens = (n: number): string => {
	if (!Number.isFinite(n) || n < 0) {
		return '0';
	}
	if (n >= 1024 * 1024) {
		return `${(n / (1024 * 1024)).toFixed(1)}M`;
	}
	if (n >= 1024) {
		return `${(n / 1024).toFixed(1)}k`;
	}
	return String(Math.round(n));
};
