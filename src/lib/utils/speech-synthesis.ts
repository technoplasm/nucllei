/**
 * ブラウザ内蔵の読み上げ(Web Speech API の speechSynthesis)で使う声を選ぶ。
 *
 * - 読む言語は画面の表示言語(i18n の language、例 ja-JP)。
 * - 使うのは端末の中で合成する声(`localService === true`)だけ。Chrome の Google 音声や
 *   Edge の Online (Natural) 音声はネットワーク越しに合成するので、応答の本文が外へ出る。
 * - 設定の voiceURI(管理者のブラウザで選んだ声)は、利用者の端末にあって上の条件を満たすときだけ使う。
 * - 条件に合う声が無ければ null(呼び手は読み上げずにエラーを出す)。
 */

type Voice = Pick<SpeechSynthesisVoice, 'voiceURI' | 'lang' | 'localService' | 'default'>;

// BCP 47 の比較用。Android は ja_JP のように _ 区切りで返すことがある
const normalizeLang = (lang: string) => lang.trim().replace(/_/g, '-').toLowerCase();
const primaryLang = (lang: string) => normalizeLang(lang).split('-')[0];

export const pickLocalVoice = <T extends Voice>(
	voices: readonly T[],
	lang: string,
	preferredVoiceURI?: string
): T | null => {
	const wanted = normalizeLang(lang);
	const candidates = voices.filter(
		(v) => v.localService === true && primaryLang(v.lang) === primaryLang(wanted)
	);
	if (candidates.length === 0) {
		return null;
	}

	const preferred = preferredVoiceURI
		? candidates.find((v) => v.voiceURI === preferredVoiceURI)
		: undefined;
	if (preferred) {
		return preferred;
	}

	// 地域まで一致する声 → その中の既定の声 → 言語だけ一致する既定の声 → 先頭
	const exact = candidates.filter((v) => normalizeLang(v.lang) === wanted);
	const pool = exact.length > 0 ? exact : candidates;
	return pool.find((v) => v.default) ?? pool[0];
};

/**
 * getVoices() は読み込みが終わるまで空で返るので、しばらく待つ。
 * 声を 1 つも持たないブラウザでは timeoutMs 後に空配列を返す(待ち続けない)。
 */
export const loadSpeechVoices = (timeoutMs = 3000): Promise<SpeechSynthesisVoice[]> =>
	new Promise((resolve) => {
		const startedAt = Date.now();
		const poll = () => {
			const voices = speechSynthesis.getVoices();
			if (voices.length > 0 || Date.now() - startedAt >= timeoutMs) {
				resolve(voices);
				return;
			}
			setTimeout(poll, 100);
		};
		poll();
	});

export const NO_LOCAL_VOICE_MESSAGE =
	'This device has no on-device voice for the display language, so the response cannot be read aloud.';
