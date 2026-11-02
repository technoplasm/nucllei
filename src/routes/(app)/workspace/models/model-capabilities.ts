// モデルの能力 (meta.capabilities) — エディタの「能力」の行に出すキーと、保存する形。
//
// 行に出すのは nucllei が読むキーだけで、既定値は backend の読み方と対:
// - vision: 既定 OFF。画像入力は明示的に有効化したモデルだけ (FE の送信前チェック・
//   backend の image_refs.is_vision_disabled)
// - audio: 既定 OFF。音声を聞けるモデルだけ添付の音声を音声のまま渡す (backend の
//   audio_refs.is_audio_model。音声を受けない API はリクエストごと落とす)
// - usage: 既定 ON。stream_options を弾く互換 API 向けの opt-out (backend の is_usage_requested)
// - preserve_reasoning: 既定 ON。tool ループの追撃に同じターンの reasoning_content を載せ直す
//   (backend の is_reasoning_preserved)
// 能力はツールを出さない — feed_audio / feed_image はツールで選んだうえで、能力のあるモデルにだけ出る
// (backend の Tools.available)。
//
// continuous_usage (backend の is_continuous_usage_requested、既定 ON) は行に出さない opt-out で、
// API で false にしたモデルだけ要求しない。エディタで保存しても保存済みの値をそのまま残す。
// それ以外のキーは nucllei のどこも読まないので、行に出さず、保存で落とす。

export const CAPABILITY_DEFAULTS = {
	vision: false,
	audio: false,
	usage: true,
	preserve_reasoning: true
} as const;

export type CapabilityKey = keyof typeof CAPABILITY_DEFAULTS;
export type EditableCapabilities = Record<CapabilityKey, boolean>;

export const CAPABILITY_KEYS = Object.keys(CAPABILITY_DEFAULTS) as CapabilityKey[];

const KEPT_CAPABILITY_KEYS = ['continuous_usage'];

type StoredCapabilities = Record<string, unknown> | null | undefined;

/** 保存済みの能力から行に出す値を作る。真偽値でないキー・無いキーは既定値 */
export const editableCapabilities = (stored: StoredCapabilities): EditableCapabilities =>
	Object.fromEntries(
		CAPABILITY_KEYS.map((key) => {
			const value = stored?.[key];
			return [key, typeof value === 'boolean' ? value : CAPABILITY_DEFAULTS[key]];
		})
	) as EditableCapabilities;

/** 保存する meta.capabilities。行の値に、行に出さない既知のキーの保存済みの値を足す */
export const capabilitiesToSave = (
	edited: EditableCapabilities,
	stored: StoredCapabilities
): Record<string, boolean> => {
	const kept = KEPT_CAPABILITY_KEYS.flatMap((key) => {
		const value = stored?.[key];
		return typeof value === 'boolean' ? [[key, value] as const] : [];
	});
	return { ...editableCapabilities(edited), ...Object.fromEntries(kept) };
};
