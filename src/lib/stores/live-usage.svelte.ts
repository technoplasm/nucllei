/**
 * 生成中のトークン使用量(#102)。
 *
 * SSE の `chat:usage`(~0.5 秒間隔、当該モデル呼び出しの累積値)を受ける揮発 state。
 * tool-progress.svelte.ts と同じ規律で **message オブジェクトの外**に置く — message に
 * 生やすと `saveChatHandler` が history を丸ごと DB に書くため「揮発」の規律が破れる。
 *
 * 全 state は **chatId でスコープ**する(#107 W-A)。subagent の子セッション等、
 * 表示中でないチャットのイベントが混ざっても、読み手(UsageChip / TurnIndicator)は
 * 自分の chatId の値しか見ない — グローバル単一スロットだと親の floating ゲージに
 * 別チャットの値が混線する。
 *
 * - live エントリ(chatId → messageId キー): ターン進行中インジケータ(TurnIndicator)と
 *   floating ゲージ(UsageChip)の生成中表示。`chat:completion` の done で破棄。
 *   tool ループでは呼び出しごとに completion_tokens が小さい値へ巻き戻る —
 *   これを新セグメントの開始と解釈してレート計算をリセットする。
 * - baseline(chatId キー): floating ゲージの確定値。`chat:completion {usage}`(backend が
 *   マージ済み message.usage を emit — ターン累積 completion/duration・最新 prompt・
 *   context_size)と、チャット読込時の leaf message usage から。チャット読込で
 *   resetChatUsage(chatId) してから seed し直す。
 *
 * ターンの所要時間・平均 tok/s は backend が `usage.duration` として永続化する —
 * 表示側(MessageActions)は `message.usage` を直接読むので、この store は持たない。
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

/** chatId → messageId → LiveUsage。 */
const entries = $state<Record<string, Record<string, LiveUsage>>>({});

/** chatId → baseline。 */
const baselines = $state<Record<string, ChatUsageBaseline>>({});

const asNumber = (value: unknown): number | undefined =>
	typeof value === 'number' && Number.isFinite(value) ? value : undefined;

/** SSE の `chat:usage`(モデル呼び出しの累積値)を適用する。 */
export const setLiveUsage = (
	chatId: string,
	messageId: string,
	payload: UsagePayload | null | undefined
) => {
	if (!chatId || !messageId || payload == null) {
		return;
	}
	const now = Date.now();
	const completion = asNumber(payload.completion_tokens) ?? 0;
	const chatEntries = (entries[chatId] ??= {});
	const previous = chatEntries[messageId];

	if (!previous) {
		chatEntries[messageId] = {
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

	chatEntries[messageId] = {
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

export const getLiveUsage = (chatId: string, messageId: string): LiveUsage | null =>
	entries[chatId]?.[messageId] ?? null;

/** 当該チャットの最新(直近サンプル)の生きているエントリ 1 件 — floating ゲージ用。 */
export const getActiveLiveUsage = (chatId: string): LiveUsage | null => {
	let latest: LiveUsage | null = null;
	for (const entry of Object.values(entries[chatId] ?? {})) {
		if (!latest || entry.sampledAt > latest.sampledAt) {
			latest = entry;
		}
	}
	return latest;
};

/** ターン終了(done)時の破棄(揮発 — 確定値は message.usage / baseline に残る)。 */
export const clearLiveUsage = (chatId: string, messageId: string) => {
	if (!chatId || !messageId) {
		return;
	}
	const chatEntries = entries[chatId];
	if (chatEntries?.[messageId]) {
		delete chatEntries[messageId];
		if (Object.keys(chatEntries).length === 0) {
			delete entries[chatId];
		}
	}
};

/**
 * floating ゲージの確定値。`chat:completion` の usage(マージ済み message.usage)、
 * またはチャット読込時の leaf message usage から。数値フィールドが 1 つも無い形は
 * 無視する(据え置き)。
 */
export const setChatUsageBaseline = (
	chatId: string,
	usage: Record<string, unknown> | null | undefined
) => {
	if (!chatId) {
		return;
	}
	if (usage == null) {
		delete baselines[chatId];
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
	baselines[chatId] = baseline;
};

export const getChatUsageBaseline = (chatId: string): ChatUsageBaseline | null =>
	baselines[chatId] ?? null;

/**
 * チャット読込時のリセット(当該チャットのみ)。baseline に加えて live エントリも
 * 捨てる — 前回表示時の残骸を持ち越さない。他チャットのスコープには触らない
 * (別チャットで走行中のターンの state を消さない)。生成中のチャットへ戻れば
 * backend が再アタッチで最新 1 件を再送してくる。
 */
export const resetChatUsage = (chatId: string) => {
	delete entries[chatId];
	delete baselines[chatId];
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

/**
 * ターンのコンテキスト使用量(占有トークン + 上限)。`message.usage` から導く —
 * MessageActions のターンごと表示用。占有の定義は UsageChip の baseline と同じ
 * `prompt_tokens ?? total_tokens`(並んだとき数字が食い違わないように揃える)。
 * 出せない形は null、context_size が無い形は contextSize: null(数値のみ表示)。
 */
export const contextUsageFromUsage = (
	usage: Record<string, unknown> | null | undefined
): { tokens: number; contextSize: number | null } | null => {
	if (usage == null) {
		return null;
	}
	const tokens = asNumber(usage.prompt_tokens) ?? asNumber(usage.total_tokens);
	if (tokens === undefined) {
		return null;
	}
	const contextSize = asNumber(usage.context_size);
	return { tokens, contextSize: contextSize !== undefined && contextSize > 0 ? contextSize : null };
};

/**
 * ターンの実測(所要秒 + 平均 tok/s)。`message.usage`(backend がターン累積で
 * 永続化した duration / completion_tokens)から導く — MessageActions の表示用。
 * 出せない形は null。
 */
export const turnStatsFromUsage = (
	usage: Record<string, unknown> | null | undefined
): { seconds: number; rate: number } | null => {
	if (usage == null) {
		return null;
	}
	const seconds = asNumber(usage.duration);
	const completion = asNumber(usage.completion_tokens);
	if (seconds === undefined || seconds <= 0 || completion === undefined || completion <= 0) {
		return null;
	}
	return { seconds, rate: completion / seconds };
};
