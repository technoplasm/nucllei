<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { tick, getContext, onMount, onDestroy, untrack } from 'svelte';
	import dayjs from 'dayjs';
	import { v4 as uuidv4 } from 'uuid';
	import { config, settings } from '$lib/stores';
	import { blobToFile } from '$lib/utils';

	import { transcribeAudio } from '$lib/apis/audio';
	import { noteInteraction } from '$lib/apis/fetch';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	const i18n = getContext('i18n');

	let {
		recording = $bindable(false),
		className = ' p-2.5 w-full max-w-full',
		draft = '',
		onConfirm = () => {},
		onTranscript = () => {},
		onCancel = () => {},
		canKeepRecording = () => false,
		onKeepRecording = async () => false
	}: {
		recording?: boolean;
		className?: string;
		// 録音を始めた時点で入力欄にあった書きかけ。確定テキストはこの末尾に足されるので、
		// 録音バーでも先頭に(薄色で)見せる。読むのはマウント時の 1 回だけ
		draft?: string;
		// send = true は「ここで切って送信」ボタン(親は入力欄へ入れたうえでそのまま送信する)
		onConfirm?: (data: { text: string; send?: boolean }) => void;
		// llens(#56): 逐次文字起こしで確定したチャンクを録音中に親へ渡す(入力欄へ追記)
		onTranscript?: (data: { text: string }) => void;
		onCancel?: () => void;
		// 通し録音(下の FULL_RECORDING_*)をこの発言の添付に残せるか。権限・配備の設定は親が
		// 知っているので聞く。false なら通し録音そのものをしない
		canKeepRecording?: (extension: string) => boolean;
		// 通し録音(長い録音は複数本)を添付としてアップロードする。完了を待ってから onConfirm
		// する(「止めて送信」は添付が付いてから送る)。1 本でも残せなかったら false。
		// isCancelled は待っている間に × で取り消されたか(親はアップロードした分を消す)
		onKeepRecording?: (
			recordings: { file: File; dictation: Dictation }[],
			isCancelled: () => boolean
		) => Promise<boolean>;
	} = $props();

	// 通し録音に付ける印(Files の meta.dictation)。配備側(llens)の tool が原本から読み直すのに
	// 使う契約で、形は backend(routers/files.py の DictationMeta)が検証する。
	// 長い録音は複数のファイルに分かれる(FULL_RECORDING_PART_SECONDS)。秒は**そのファイルの
	// 先頭を 0 とした実時刻**で、区間は「区切り用の recorder が録っていた時間」— 発話を待っていた
	// 無音の間は区間と区間の間が空く
	type DictationCut = 'breath' | 'forced' | 'silence' | 'end';
	type DictationSegment = {
		// 録音全体の通し番号(ファイルをまたいで続く)
		i: number;
		start_sec: number;
		end_sec: number;
		// breath = 目安を過ぎて息継ぎで切れた / forced = 上限で強制 /
		// silence = 読み取り猶予の無音で区切れた / end = 確定・送信で閉じた最終区間
		cut: DictationCut;
		// その区間の文字起こし全文(短すぎて送らなかった区間・失敗した区間は空)
		text: string;
	};
	type Dictation = {
		version: 1;
		// 同じ録音から分かれたファイルに共通の id と、その何本目か(1 始まり)/ 全部で何本か
		session_id: string;
		part: number;
		parts: number;
		// このファイルが録音全体の何秒目から始まるか
		offset_sec: number;
		duration_sec: number;
		container: string;
		segments: DictationSegment[];
		// このファイルに含まれる区間ぶんの連結文
		joined_text: string;
	};

	// llens(#56): 逐次文字起こし(疑似ストリーミング)のパラメータ。
	// 既定値は Settings > Chats > Voice の同名行と揃える(根拠は _memos/audio.md)
	const DEFAULT_SILENCE_TIMEOUT = 4000;
	const DEFAULT_MIN_CHUNK_DURATION = 300;

	// 周期確定のパラメータ。根拠は _memos/audio.md「周期確定」。
	// 喋り続けていても一定の周期でセグメントを切り、その分だけを転写して確定テキストとして
	// 足していく(同じ音声を 2 度転写しない)。周期は録音バーに進捗として見せる。
	// - SOFT(周期の目安)を過ぎたら、次の短い息継ぎ(CUT_PAUSE の無音)で切る。
	//   語の途中で切ると、切れた語が前後どちらのセグメントでも誤認識されるため
	// - 息継ぎが無いまま HARD に達したら強制的に切る(語頭・語尾が欠けうる)
	// 値は日本語音声での実験(2026-10-02)で決めた。速さより安定性(区切りで文が壊れないこと)を
	// 取っている(ken 決定)。区切りで文が壊れた件数/分(切らない転写との差)と、発話から表示までの遅れ:
	// SOFT = 12 秒: 8 秒 → 12 秒で破綻が約半分(息継ぎのある音声 0.91 → 0.50 件/分、切れ目なし
	//   1.99 → 1.25)。効くのは遅い話者 — 8 秒だと SOFT から 4 秒の間に息継ぎが来ずに強制切りに
	//   当たる(1.44 → 0.32)。16 秒は 12 秒より良くならず遅れだけ増え、4 秒は破綻が倍。
	//   代償は遅れ: 発話から表示まで平均 13.5 → 17.4 秒(転写 4 秒/回での試算)
	// HARD = SOFT + 4 秒 = 16 秒: 強制切りは語の途中に当たり、4〜5 割で文が壊れる(息継ぎ切りは
	//   1 割前後)。息継ぎを待つ猶予が 4 秒。喋った音は遅くとも 16 秒後には転写に回る
	// CUT_PAUSE = 400ms: 句読点・息継ぎの間は概ね 300〜700ms、語中の無音(促音・破裂音の
	//   閉鎖)は 150ms 未満。その間に置いた値。300ms にすると強制切りは減るが(19% → 8%)、
	//   語中で切れる害を確かめていないので据え置き
	// SOFT・CUT_PAUSE・無音判定のしきい値・通し録音のビットレートは配備ごとに env で動かせる
	// (/api/config の audio.stt。範囲の丸めはサーバ側 — nucllei/config.py)。ここの数字は
	// 応答に無いときの既定で、サーバの既定と同じ値。録音の途中では変わらないので 1 度だけ読む
	const sttConfig = untrack(() => $config?.audio?.stt);
	const SEGMENT_SOFT_LIMIT = (sttConfig?.segment_seconds ?? 12) * 1000;
	const SEGMENT_HARD_LIMIT = SEGMENT_SOFT_LIMIT + 4000;
	const SEGMENT_CUT_PAUSE = sttConfig?.segment_pause_ms ?? 400;

	// 通し録音: 区切り用の recorder とは別に、録音の最初から最後までを録り、区切りが 2 つ以上
	// あった録音だけ、その発言の添付に残す(区切りで壊れた語を、配備側の tool が原本から
	// 読み直せるように。_memos/audio.md「通し録音の添付」)。区切りごとのファイルは各自ヘッダを
	// 持つので連結できない — だから別に録る。
	// ビットレートは Opus mono で実測して決めた(同メモの表)。日本語音声 31 本を 1 本通しで
	// 転写すると、12 / 16 / 24 kbps のどれも無圧縮と精度の差は出なかった(差は Whisper 自体の
	// 揺れに埋もれる)。人の朗読では 12kbps だけ無圧縮の転写からのずれが大きめだったので、
	// 読み直し用の原本として 1 段余裕を見て 24kbps。Chrome の実ファイルは指定の約 9 割
	// (24k 指定で 21.6kbps = 毎秒 約 2700 バイト)
	const FULL_RECORDING_BITRATE = (sttConfig?.recording_bitrate_kbps ?? 24) * 1000;
	const FULL_RECORDING_BYTES_PER_SEC = (FULL_RECORDING_BITRATE * 0.9) / 8;
	// 通し録音は約 60 分ごとに次のファイルへ分ける(録音そのものは止めない。利用者には見せない)。
	// 添付の上限(ATTACHMENT_MAX_SIZE_MB、既定 25MB)には上のビットレートで約 160 分入るので、
	// サイズより先に「読み直しにかかる時間」で決めている: 1 本全体の読み直しは実時間の約 1/4
	// (60 分で約 15 分)かかる。配備の添付上限から逆算した時間の方が短ければそちらで分ける
	// (ビットレートを守らないブラウザと、切れ目を待つ間の超過に備えて 2 割の余裕を見る)
	const FULL_RECORDING_PART_SECONDS = 60 * 60;
	const FULL_RECORDING_SIZE_MARGIN = 0.8;
	// 次のファイルへ切り替えるとき、前のファイルを止めるのをこれだけ遅らせる。MediaRecorder は
	// stop() した瞬間の末尾 数十 ms を書き出さない(実測 40〜50ms)ので、同時に止めると切れ目の
	// 直前の音がどちらのファイルにも残らない。前のファイルの末尾が次の先頭と重なる分には、
	// 区間(segments)は重ならないので害が無い
	const FULL_RECORDING_PART_OVERLAP = 300;

	// 発話を検出している間、アイドルタイムアウト(apis/fetch.ts)へ「人が居る」と知らせる間隔
	const VOICE_ACTIVITY_INTERVAL = 5000;

	const silenceTimeout = $derived($settings?.voiceSilenceTimeout ?? DEFAULT_SILENCE_TIMEOUT);
	const minChunkDuration = $derived($settings?.voiceMinChunkDuration ?? DEFAULT_MIN_CHUNK_DURATION);

	let loading = $state(false);

	let durationSeconds = $state(0);
	let durationCounter: ReturnType<typeof setInterval> | undefined;

	let transcription = '';

	// 録音を始めた時点の書きかけ(以後の親の入力欄の変化 = この録音の追記分は追わない)
	const draftText = untrack(() => draft);
	// 録音中に確定したテキスト(入力欄は録音バーに隠れるので、ここにも出して見せる)。
	// 確定した単位ごとに持ち、足された瞬間だけ軽く強調する
	let confirmedSegments = $state<string[]>([]);
	// Web Speech 経路の認識途中のテキスト(未確定)。確定の結果が来たら消える
	let interimText = $state('');
	// 現セグメントを録り始めてからの経過 ms(0 = 録っていない = 発話待ち)。周期の進捗表示用
	let cycleAge = $state(0);
	// 外部 STT 経路(周期の進捗・文字起こし中の表示を出す)か。録音開始時に決まる
	let showsCycle = $state(false);
	let transcriptElement = $state<HTMLDivElement | null>(null);

	const startDurationCounter = () => {
		durationSeconds = 0;
		durationCounter = setInterval(() => {
			durationSeconds++;
		}, 1000);
	};

	// 止めるだけで値は戻さない: マイクを放してから通し録音のアップロードを待つ間も録音バーは
	// 出ているので、止めた時点の経過時間を見せたまま閉じる(0 に戻すのは次に録り始めるとき)
	const stopDurationCounter = () => {
		clearInterval(durationCounter);
	};

	// recording の on/off で録音を起動/停止する(マウント時は recording=true で開始)。
	// start/stop 内の状態読み書きで effect が再発火しないよう untrack で包む(runes 化の追跡罠)。
	$effect(() => {
		if (recording) {
			untrack(() => startRecording());
		} else {
			untrack(() => stopRecording());
		}
	});

	// テキスト(確定・未確定)が伸びたら末尾へスクロール
	$effect(() => {
		confirmedSegments.length;
		interimText;
		if (transcriptElement) {
			transcriptElement.scrollTop = transcriptElement.scrollHeight;
		}
	});

	const formatSeconds = (seconds: number) => {
		const minutes = Math.floor(seconds / 60);
		const remainingSeconds = seconds % 60;
		const formattedSeconds = remainingSeconds < 10 ? `0${remainingSeconds}` : remainingSeconds;
		return `${minutes}:${formattedSeconds}`;
	};

	// Web Speech API は lib.dom に型が無いため、使用箇所に必要な最小限のローカル型を置く
	type SpeechRecognitionLike = {
		continuous: boolean;
		interimResults: boolean;
		start: () => void;
		stop: () => void;
		onresult:
			| ((event: {
					resultIndex: number;
					results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }>;
			  }) => void)
			| null;
		onend: (() => void) | null;
		onerror: ((event: { error: string }) => void) | null;
	};

	let stream: MediaStream | null = null;
	let audioContext: AudioContext | null = null;
	let speechRecognition: SpeechRecognitionLike | undefined;

	let mediaRecorder: MediaRecorder | undefined;
	let audioChunks: Blob[] = [];
	// 通し録音。長い録音は複数のファイル(part)に分かれるので recorder ごとに持つ。
	// recording は stop 後に 1 本の Blob で解決する(録れていなければ null)
	type FullPart = {
		recorder: MediaRecorder;
		recording: Promise<Blob | null>;
		startedAt: number;
		stoppedAt: number;
		// この part に入った区間(秒は part の先頭から)
		segments: DictationSegment[];
	};
	let fullParts: FullPart[] = [];
	// 録っている最中の part(通し録音をしていない・止めた後は undefined)
	let fullPart: FullPart | undefined;
	// 1 つの part の長さの上限(ms)。録音開始時に決まる
	let fullPartLimit = FULL_RECORDING_PART_SECONDS * 1000;
	// 区間の通し番号(part をまたいで続く)と、実際に文字起こしへ送った区間の数
	let segmentCount = 0;
	let transcribedSegments = 0;
	// 現セグメントを止めた時刻・止めた理由・そのセグメントが入っている part
	let segmentStoppedAt = 0;
	let segmentCut: DictationCut = 'end';
	let segmentPart: FullPart | undefined;
	// × で取り消された(通し録音のアップロードを待っている間でも取り消せる)
	let cancelled = false;
	// 通し録音をアップロード中(録音バーの「録音を保存中」表示)
	let saving = $state(false);
	// 現セグメントの録音開始時刻(0 = まだ録っていない)
	let segmentStartedAt = 0;
	// 現セグメントで最後に音を検出した時刻。minChunkDuration は「録音していた長さ」ではなく
	// **発話区間の長さ**(= 末尾の無音猶予を含まない)で判定するのでこれを使う
	let segmentEndedAt = 0;
	// 確定ボタン後の最終セグメント処理中(この stop では次のセグメントを起こさない)
	let finishing = false;
	// stop() 済みで onstop 待ち(無音カットと確定ボタンが同フレームで競合するのを防ぐ)
	let stopping = false;
	// finish() は 1 度だけ(onConfirm の二重発火 = 入力欄への二重挿入を防ぐ)
	let finalized = false;
	// キャンセル/停止/破棄後に遅れて返ってきた転写を捨てるためのフラグ。
	// rAF ループの継続判定もこれで見る(recording prop はルート遷移で破棄されても
	// true のまま残るため、あれに依存すると破棄後もループが回り続ける)
	let aborted = false;
	// STT 失敗のトーストは 1 録音につき 1 回だけ(逐次化で発話の切れ目ごとに出ると積み上がる)
	let sttErrorNotified = false;
	// コンポーネント破棄済み(ルート遷移等)。破棄経路でだけ Web Speech のハンドラを外す
	let destroyed = false;
	// チャンクの POST は順序保証が要る(並列に投げると文が入れ替わる)ので直列キューに積む
	let transcribeQueue: Promise<void> = Promise.resolve();
	// キューに積まれて未完了の転写の数(録音バーの「文字起こし中」表示)
	let pendingSegments = $state(0);
	// 周期の切れ目で止めた(= 発話の途中)。onstop で次のセグメントを音を待たずに録り始める
	let resumeAfterCut = false;
	// 「ここで切って送信」で確定した(finish() が onConfirm へ send: true を載せる)
	let sendOnFinish = false;

	// 無音判定のしきい値(これより小さい音は無音)。入力の音量に強く依存する — マイクによって
	// 調整が要る値(_memos/audio.md「配備ごとの調整」)
	const MIN_DECIBELS = sttConfig?.silence_threshold_db ?? -45;
	let VISUALIZER_BUFFER_LENGTH = 300;

	let visualizerData = $state(Array(VISUALIZER_BUFFER_LENGTH).fill(0));

	const usesWebSpeech = () =>
		$config?.audio?.stt?.engine === 'web' || ($settings?.audio?.stt?.engine ?? '') === 'web';

	// llens(#56): backend の許可 MIME(audio/webm|ogg|mp4|wav|mpeg|x-m4a)に載る型を選ぶ。
	// 選べなければブラウザ既定に任せ、実際の MIME をそのまま送る(wav 詐称はしない)。
	// Safari は webm/ogg を作れず既定の audio/mp4 になるが、それも許可リストに載っている。
	const pickMimeType = () => {
		if (typeof MediaRecorder === 'undefined' || !MediaRecorder.isTypeSupported) {
			return '';
		}
		for (const type of ['audio/webm', 'audio/ogg']) {
			if (MediaRecorder.isTypeSupported(type)) {
				return type;
			}
		}
		return '';
	};

	// 区切り用の recorder を止める(理由・時刻・入っている part を、通し録音の区間一覧のために
	// 控える)。通し録音の part が上限を過ぎていたら、この切れ目で次の part へ切り替える —
	// 1 つの区間が 2 つのファイルにまたがらない
	const stopSegment = (cut: DictationCut) => {
		if (!mediaRecorder) return;
		stopping = true;
		segmentCut = cut;
		segmentStoppedAt = Date.now();
		segmentPart = fullPart;
		mediaRecorder.stop();

		if (cut !== 'end' && fullPart && segmentStoppedAt - fullPart.startedAt > fullPartLimit) {
			rotateFullPart();
		}
	};

	// ms の差を秒へ(ms 精度)
	const toSeconds = (ms: number) => Math.max(0, Math.round(ms)) / 1000;

	const extensionFor = (mimeType: string) => {
		const base = (mimeType ?? '').split(';')[0];
		return (
			{
				'audio/webm': 'webm',
				'audio/ogg': 'ogg',
				'audio/mp4': 'mp4',
				'audio/mpeg': 'mp3',
				'audio/wav': 'wav'
			}[base] ?? 'webm'
		);
	};

	// Function to calculate the RMS level from time domain data
	const calculateRMS = (data: Uint8Array) => {
		let sumSquares = 0;
		for (let i = 0; i < data.length; i++) {
			const normalizedValue = (data[i] - 128) / 128; // Normalize the data
			sumSquares += normalizedValue * normalizedValue;
		}
		return Math.sqrt(sumSquares / data.length);
	};

	const normalizeRMS = (rms: number) => {
		rms = rms * 10;
		const exp = 1.5; // Adjust exponent value; values greater than 1 expand larger numbers more and compress smaller numbers more
		const scaledRMS = Math.pow(rms, exp);

		// Scale between 0.01 (1%) and 1.0 (100%)
		return Math.min(1.0, Math.max(0.01, scaledRMS));
	};

	// 波形の描画と、発話区間の切り出し(#56)を 1 本の rAF ループで回す。
	// 確定の単位はセグメント(webm/opus は先頭にしかヘッダが無く、timeslice で刻んだ 2 個目
	// 以降のチャンクは単体でデコードできないため)— 無音の切れ目か周期の切れ目で
	// MediaRecorder を stop → 転写キューへ積む → すぐ次のセグメントを起こす
	// (CallOverlay の stopRecordingCallback と同じ形)。
	const analyseAudio = (stream: MediaStream) => {
		audioContext = new AudioContext();
		const audioStreamSource = audioContext.createMediaStreamSource(stream);

		const analyser = audioContext.createAnalyser();
		// minDecibels は maxDecibels(既定 -30)未満でないと例外になる。しきい値を -30 以上に
		// 上げた配備のために上側を先に広げる(音の有無は「0 を超える bin があるか」だけで見るので、
		// 上側の値は判定に影響しない)
		analyser.maxDecibels = Math.max(analyser.maxDecibels, MIN_DECIBELS + 15);
		analyser.minDecibels = MIN_DECIBELS;
		audioStreamSource.connect(analyser);

		const bufferLength = analyser.frequencyBinCount;

		const domainData = new Uint8Array(bufferLength);
		const timeDomainData = new Uint8Array(analyser.fftSize);

		let lastSoundTime = Date.now();
		let speaking = false;
		// 最後に「声 = 人の操作」と記録した時刻(下の VOICE_ACTIVITY_INTERVAL で間引く)
		let lastVoiceActivityAt = 0;

		const detectSound = () => {
			const processFrame = () => {
				// aborted = stopRecording() 済み(キャンセル / 確定 / コンポーネント破棄)。
				// recording prop はルート遷移で破棄されても true のままなので、継続判定を
				// あれだけに依存させるとループが生き残って転写を投げ続ける
				if (aborted || !recording || loading) return;

				analyser.getByteTimeDomainData(timeDomainData);
				analyser.getByteFrequencyData(domainData);

				// Calculate RMS level from time domain data
				const rmsLevel = calculateRMS(timeDomainData);
				// Push the calculated decibel level to visualizerData
				visualizerData.push(normalizeRMS(rmsLevel));

				// Ensure visualizerData array stays within the buffer length
				if (visualizerData.length >= VISUALIZER_BUFFER_LENGTH) {
					visualizerData.shift();
				}

				visualizerData = visualizerData;

				const hasSound = domainData.some((value) => value > 0);
				if (hasSound) {
					speaking = true;
					lastSoundTime = Date.now();

					// 喋っている間はキーもクリックも無い。声を「人の操作」として記録し、長い口述の
					// 途中でアイドルタイムアウトに追い出されないようにする(Web Speech 経路もこの
					// ループを回すので同じ扱い)。無音の間は呼ばない — 録音バーを開いたまま離席した
					// 共用 PC は今までどおり切れる
					if (lastSoundTime - lastVoiceActivityAt > VOICE_ACTIVITY_INTERVAL) {
						lastVoiceActivityAt = lastSoundTime;
						noteInteraction();
					}

					// 発話が始まったところで録音開始(無音だけのセグメントを作らない)。
					// stop() 済みで onstop 待ち(stopping)の recorder は起こさない —
					// 起こすと ① segmentStartedAt が上書きされて直前の発話が
					// minChunkDuration 未満と判定され捨てられる ② 古い recorder が
					// 誰にも止められず残る ③ 共有している audioChunks に別ストリームの
					// チャンクが混ざって壊れた webm になる。confirmRecording() が
					// 同じ窓を !stopping で守っているのと同じ理由
					if (!stopping && mediaRecorder && mediaRecorder.state === 'inactive') {
						mediaRecorder.start();
						segmentStartedAt = lastSoundTime;
						segmentEndedAt = lastSoundTime;
					}

					// 発話区間の終端。stop() 後は state が inactive なので更新されず、
					// 「最後に音がした時刻」がそのセグメントのものとして残る
					if (mediaRecorder && mediaRecorder.state === 'recording') {
						segmentEndedAt = lastSoundTime;
					}
				}

				// 「読み取り猶予」ぶん無音が続いたら発話の区切りとみなす
				const now = Date.now();
				if (speaking && now - lastSoundTime > silenceTimeout) {
					speaking = false;
					if (mediaRecorder && mediaRecorder.state === 'recording') {
						stopSegment('silence');
					}
				}

				// 周期の切れ目(SEGMENT_* の定数コメント参照)。確定して切るだけで speaking は
				// 落とさない。次のセグメントは onstop がその場で録り始める(resumeAfterCut)
				cycleAge = 0;
				if (!stopping && mediaRecorder && mediaRecorder.state === 'recording') {
					const age = now - segmentStartedAt;
					cycleAge = age;
					const atBreath = age > SEGMENT_SOFT_LIMIT && now - lastSoundTime > SEGMENT_CUT_PAUSE;
					if (atBreath || age > SEGMENT_HARD_LIMIT) {
						resumeAfterCut = true;
						stopSegment(atBreath ? 'breath' : 'forced');
					}
				}

				// 発話を待っている間(どの区間も録っていない)に通し録音の part が上限を過ぎたら、
				// 次の切れ目を待たずにここで切り替える(黙ったまま放置された録音を伸ばし続けない)
				if (
					fullPart &&
					!stopping &&
					mediaRecorder?.state === 'inactive' &&
					now - fullPart.startedAt > fullPartLimit
				) {
					rotateFullPart();
				}

				window.requestAnimationFrame(processFrame);
			};

			window.requestAnimationFrame(processFrame);
		};

		detectSound();
	};

	// セグメントは 1 つずつ単独で転写する。直前の確定文を Whisper の prompt として渡す方式は
	// 試して捨てた(偽の単位・幻聴・誤りの伝播 — _memos/audio.md「捨てた方式: 直前の確定文を…」)
	const transcribeSegment = async (chunks: Blob[], mimeType: string) => {
		await tick();
		const file = blobToFile(
			new Blob(chunks, { type: mimeType }),
			`recording.${extensionFor(mimeType)}`
		);

		const res = await transcribeAudio(file).catch((error) => {
			// 逐次化でセグメントごと(十数秒に 1 回以上)に呼ばれるため、STT が落ちている間
			// 喋り続けるとトーストが積み上がる。1 録音セッションにつき 1 回に抑える
			if (!sttErrorNotified) {
				sttErrorNotified = true;
				toast.error(`${error}`);
			}
			return null;
		});

		return `${res?.text ?? ''}`.trim();
	};

	// 転写は直列キュー。並列に投げると順不同で追記され文が壊れる。
	// キューは必ず解決状態へ戻す(1 度 reject させると以後の .then が全部スキップされ、
	// 確定しても finish() が呼ばれず録音バーがスピナーのまま固まる)
	const enqueueSegment = (
		chunks: Blob[],
		duration: number,
		mimeType: string,
		isFinal: boolean,
		record?: DictationSegment
	) => {
		pendingSegments += 1;
		transcribeQueue = transcribeQueue
			.then(async () => {
				let text = '';

				try {
					// 発話区間が短すぎるチャンクは精度が落ちるので送らない
					// (咳払い・物音のような単発の音もここで落ちる)
					if (!aborted && chunks.length > 0 && duration >= minChunkDuration) {
						transcribedSegments += 1;
						text = await transcribeSegment(chunks, mimeType);
						if (record) record.text = text;
					}
				} finally {
					pendingSegments -= 1;
				}

				if (aborted) return;

				if (isFinal) {
					await finish(text);
				} else if (text) {
					confirmedSegments.push(`${text} `);
					onTranscript({ text });
				}
			})
			.catch(async (error) => {
				console.error('VoiceRecording: transcription segment failed', error);
				// 最終セグメントで落ちても録音 UI は必ず閉じる(× でしか抜けられなくなるのを防ぐ)
				if (isFinal && !aborted) {
					await finish('').catch((finishError) => {
						console.error('VoiceRecording: finish failed', finishError);
					});
				}
			});
	};

	const startSegment = () => {
		if (!stream || aborted) return;

		const mimeType = pickMimeType();
		const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);

		audioChunks = [];
		segmentStartedAt = 0;
		segmentEndedAt = 0;

		recorder.ondataavailable = (event) => {
			if (event.data.size > 0) {
				audioChunks.push(event.data);
			}
		};

		recorder.onstop = () => {
			stopping = false;
			const chunks = audioChunks.slice(0);
			// minChunkDuration の判定は「録音していた長さ」ではなく**発話区間の長さ**で測る。
			// 無音カットの stop は lastSoundTime + silenceTimeout に来るので、経過時間で
			// 測ると常に silenceTimeout(既定 4000ms)以上になり、既定の minChunkDuration
			// (1000ms)では判定が常に真になって物音でも 1 セグメント飛んでいた。
			// 確定ボタンで閉じる最終セグメントも同じ式でよい(recorder は音を検出した
			// ところでしか start しないので segmentEndedAt >= segmentStartedAt が保証される)
			const duration = segmentStartedAt === 0 ? 0 : Math.max(0, segmentEndedAt - segmentStartedAt);
			const isFinal = finishing;

			// 通し録音の区間一覧に足す(短すぎて転写へ送らない区間も載せる — text は空のまま)
			let record: DictationSegment | undefined;
			if (segmentPart && segmentStartedAt !== 0 && !aborted) {
				segmentCount += 1;
				record = {
					i: segmentCount,
					start_sec: toSeconds(segmentStartedAt - segmentPart.startedAt),
					end_sec: toSeconds(segmentStoppedAt - segmentPart.startedAt),
					cut: segmentCut,
					text: ''
				};
				segmentPart.segments.push(record);
			}

			audioChunks = [];
			segmentStartedAt = 0;
			segmentEndedAt = 0;

			// 次のセグメントを先に用意してから転写を積む(取りこぼしを最小化)
			if (!isFinal) {
				startSegment();

				// 周期の切れ目で止めたときは発話の途中なので、音の検出を待たずに録り始める。
				// 待つと、検出 → start() の遅れのぶん次の語頭が欠ける(実測: 息継ぎで切った
				// 直後の "We advised" が "Badvised" になった)。このあと音が来なければ
				// 発話区間は 0 のままで、読み取り猶予で止まったときに転写へは送られない
				if (resumeAfterCut && mediaRecorder) {
					mediaRecorder.start();
					segmentStartedAt = Date.now();
					segmentEndedAt = segmentStartedAt;
				}
			}
			resumeAfterCut = false;

			enqueueSegment(chunks, duration, recorder.mimeType, isFinal, record);
		};

		mediaRecorder = recorder;
	};

	// 通し録音の part を 1 つ起こす。区切り用と同じ stream・同じ MIME の選び方(Chrome / Edge /
	// Firefox は webm の Opus、Safari は audio/mp4)で、ビットレートだけ下げる
	const startFullPart = (_stream: MediaStream, mimeType: string) => {
		const recorder = new MediaRecorder(_stream, {
			...(mimeType ? { mimeType } : {}),
			audioBitsPerSecond: FULL_RECORDING_BITRATE
		});
		const chunks: Blob[] = [];
		recorder.ondataavailable = (event) => {
			if (event.data.size > 0) {
				chunks.push(event.data);
			}
		};
		// stop() のあと、最後の dataavailable → stop の順に来る。Blob は stop で組む
		const recording = new Promise<Blob | null>((resolve) => {
			recorder.onstop = () =>
				resolve(chunks.length > 0 ? new Blob(chunks, { type: recorder.mimeType }) : null);
		});
		recorder.start();

		fullPart = { recorder, recording, startedAt: Date.now(), stoppedAt: 0, segments: [] };
		fullParts.push(fullPart);
	};

	// 通し録音を始める。起こせなければ残さないだけで、音声入力そのものは続ける
	const startFullRecording = (_stream: MediaStream) => {
		const mimeType = pickMimeType();
		if (!canKeepRecording(extensionFor(mimeType || 'audio/mp4'))) return;

		// 配備の添付上限が小さいときは、サイズから逆算した時間で分ける
		const maxSize = $config?.file?.max_size ?? null;
		if (maxSize) {
			fullPartLimit = Math.min(
				fullPartLimit,
				Math.floor(
					(maxSize * 1024 * 1024 * FULL_RECORDING_SIZE_MARGIN) / FULL_RECORDING_BYTES_PER_SEC
				) * 1000
			);
		}

		try {
			startFullPart(_stream, mimeType);
		} catch (error) {
			console.error('VoiceRecording: full recording unavailable', error);
		}
	};

	const stopFullPart = (part: FullPart) => {
		if (part.recorder.state !== 'inactive') {
			part.stoppedAt = Date.now();
			part.recorder.stop();
		}
	};

	// 通し録音を止める(切り替え直後で、止めるのを遅らせている前の part も含めて)。
	// トラックを止める前に呼ぶ(末尾まで書き出させる)
	const stopFullRecording = () => {
		fullParts.forEach(stopFullPart);
		fullPart = undefined;
	};

	// 次の part へ切り替える。次の recorder を先に起こし、前は少し遅れて止める(切り替えの
	// 瞬間の音を欠かさない — FULL_RECORDING_PART_OVERLAP)。起こせなければ今の part を
	// そのまま続ける
	const rotateFullPart = () => {
		const previous = fullPart;
		if (!previous || !stream) return;

		try {
			startFullPart(stream, pickMimeType());
		} catch (error) {
			console.error('VoiceRecording: starting the next recording part failed', error);
			return;
		}
		setTimeout(() => stopFullPart(previous), FULL_RECORDING_PART_OVERLAP);
	};

	// 通し録音を、この発言の添付に残す。区切りが 1 つだけの録音は残さない(1 回で文字起こし
	// 済みで、読み直しても同じ結果になる — 短い指示の大半はこれ)。残せなかったら知らせるだけで、
	// 文字起こしの結果はそのまま入力欄へ入る
	const keepFullRecording = async () => {
		const recorded = await Promise.all(
			fullParts.map(async (part) => ({ part, blob: await part.recording }))
		);
		if (transcribedSegments < 2) return;

		// 区間が 1 つも入っていない part(切り替えた直後に確定した等)は残さない
		const parts = recorded.filter(({ part, blob }) => blob && part.segments.length > 0);
		if (parts.length === 0) return;

		const sessionId = uuidv4();
		const sessionStartedAt = fullParts[0].startedAt;
		const baseName = `${$i18n.t('Voice input')} ${dayjs(sessionStartedAt).format('YYYY-MM-DD HHmm')}`;

		const recordings = parts.map(({ part, blob }, index) => {
			const mimeType = (blob as Blob).type.split(';')[0];
			const extension = extensionFor(mimeType);
			const dictation: Dictation = {
				version: 1,
				session_id: sessionId,
				part: index + 1,
				parts: parts.length,
				offset_sec: toSeconds(part.startedAt - sessionStartedAt),
				duration_sec: toSeconds(part.stoppedAt - part.startedAt),
				container: { webm: 'webm/opus', ogg: 'ogg/opus', mp4: 'mp4/aac' }[extension] ?? extension,
				segments: part.segments,
				joined_text: part.segments
					.map((segment) => segment.text)
					.filter((text) => text !== '')
					.join(' ')
			};
			const name =
				parts.length > 1
					? `${baseName} (${index + 1}-${parts.length}).${extension}`
					: `${baseName}.${extension}`;

			return { file: new File([blob as Blob], name, { type: mimeType }), dictation };
		});

		saving = true;
		const kept = await onKeepRecording(recordings, () => cancelled || destroyed).catch((error) => {
			console.error('VoiceRecording: keeping the recording failed', error);
			return false;
		});
		saving = false;

		if (!kept && !cancelled && !destroyed) {
			toast.error($i18n.t('The recording could not be attached. The transcribed text is kept.'));
		}
	};

	// 最終セグメントの転写が終わったところで録音 UI を閉じる
	const finish = async (text: string) => {
		if (finalized) return;
		finalized = true;

		cycleAge = 0;

		// 通し録音は確定ボタンで止めてある(Web Speech 経路には無い)。マイクを放す前に
		// 止まっていることだけ確かめる
		stopFullRecording();

		// 親が recording=false でこのコンポーネントを破棄するので、
		// マイクの解放はここで明示的にやる(effect の後片付けには頼らない)
		await stopRecording();

		// アップロードが終わるまで録音バーは締めの表示のまま待つ(「止めて送信」は添付が
		// 付いてから送る)。待っている間に × で取り消されたら何も入れない
		await keepFullRecording();
		if (cancelled || destroyed) return;

		finishing = false;
		loading = false;
		recording = false;

		onConfirm({ text, send: sendOnFinish });
	};

	const startRecording = async () => {
		// llens(#56): http 配備では navigator.mediaDevices が生えない(secure context 必須)。
		// 例外で無反応になる前に理由を出して閉じる
		if (!navigator.mediaDevices?.getUserMedia) {
			toast.error($i18n.t('Microphone requires a secure (HTTPS) connection.'));
			onCancel();
			return;
		}

		loading = true;

		const _stream = await navigator.mediaDevices
			.getUserMedia({
				audio: {
					// mono で足りる(STT は mono に落とす。通し録音のサイズも半分で済む)
					channelCount: 1,
					echoCancellation: true,
					noiseSuppression: true,
					autoGainControl: true
				}
			})
			.catch((error) => {
				toast.error($i18n.t(`Permission denied when accessing microphone: {{error}}`, { error }));
				return null;
			});

		if (!_stream) {
			loading = false;
			onCancel();
			return;
		}

		// 権限ダイアログを出している間にキャンセル/ルート遷移で破棄されると、後片付け
		// (onDestroy → stopRecording)はもう済んでいる。受け取ったばかりの stream を
		// そのまま持つとマイクが開きっぱなしになるので、ここで止めて何も始めない
		if (destroyed || aborted) {
			_stream.getTracks().forEach((track) => track.stop());
			loading = false;
			return;
		}

		stream = _stream;
		loading = false;
		startDurationCounter();

		if (usesWebSpeech()) {
			// Web Speech は録音データを使わない(波形表示のためだけに analyser を回す)
			startWebSpeechRecognition();
			analyseAudio(_stream);
			return;
		}

		showsCycle = true;
		startFullRecording(_stream);
		startSegment();
		analyseAudio(_stream);
	};

	const startWebSpeechRecognition = () => {
		// lib.dom に無い prefixed コンストラクタを型だけ拡張して存在チェック(挙動は従来の in 判定と同じ)
		const speechWindow = window as typeof window & {
			SpeechRecognition?: new () => SpeechRecognitionLike;
			webkitSpeechRecognition?: new () => SpeechRecognitionLike;
		};
		const SpeechRecognitionCtor =
			speechWindow.SpeechRecognition || speechWindow.webkitSpeechRecognition;
		if (!SpeechRecognitionCtor) return;

		// Create a SpeechRecognition object
		speechRecognition = new SpeechRecognitionCtor();

		// Set continuous to true for continuous recognition
		speechRecognition.continuous = true;
		// 認識途中の結果も受け取り、外部 STT 経路と同じ未確定表示に載せる
		speechRecognition.interimResults = true;

		let timeoutId: ReturnType<typeof setTimeout> | undefined;
		// Start recognition
		speechRecognition.start();

		// Event triggered when speech is recognized
		speechRecognition.onresult = async (event) => {
			// Clear the inactivity timeout
			clearTimeout(timeoutId);

			// Handle recognized speech: 確定した結果(isFinal)だけを transcription へ積み、
			// 認識途中のものは未確定テキストとして表示するだけにする
			let interim = '';
			for (let i = event.resultIndex; i < event.results.length; i++) {
				const result = event.results[i];
				if (result.isFinal) {
					transcription = `${transcription}${result[0].transcript}`;
				} else {
					interim = `${interim}${result[0].transcript}`;
				}
			}

			confirmedSegments = transcription ? [transcription] : [];
			interimText = interim;

			await tick();
			document.getElementById('chat-input')?.focus();

			// 「読み取り猶予」ぶん無音が続いたら打ち切る(外部 STT の区切りと同じ設定を使う)
			timeoutId = setTimeout(() => {
				speechRecognition?.stop();
			}, silenceTimeout);
		};

		// Event triggered when recognition is ended
		speechRecognition.onend = function () {
			finish(transcription);
		};

		// Event triggered when an error occurs
		speechRecognition.onerror = function (event) {
			toast.error($i18n.t(`Speech recognition error: {{error}}`, { error: event.error }));
			onCancel();

			stopRecording();
		};
	};

	// 録音資源の解放。キャンセル / 確定 / コンポーネント破棄のどこからでも、何度呼ばれても
	// 安全(解放済みは undefined / null で素通りする)
	const stopRecording = async () => {
		aborted = true;

		if (mediaRecorder && mediaRecorder.state !== 'inactive') {
			mediaRecorder.stop();
		}
		mediaRecorder = undefined;

		// キャンセル / 破棄ではここで止まり、Blob は誰にも受け取られずに捨てられる
		stopFullRecording();

		if (speechRecognition) {
			const recognition = speechRecognition;
			// 破棄経路ではハンドラを外してから止める。onend → finish() → onConfirm() が
			// 走ると、消えたコンポーネントから親の入力欄へ書き込むことになる。
			// 通常のキャンセル/確定経路では従来どおりハンドラを残す
			// (Web Speech の確定は onend → finish() で閉じるため)
			if (destroyed) {
				speechRecognition = undefined;
				recognition.onresult = null;
				recognition.onend = null;
				recognition.onerror = null;
			}
			recognition.stop();
		}

		stopDurationCounter();
		audioChunks = [];

		if (stream) {
			const tracks = stream.getTracks();
			tracks.forEach((track) => track.stop());
		}

		stream = null;

		audioContext?.close();
		audioContext = null;
	};

	// 確定ボタン: 最後のセグメントをキューへ流し、drain したら finish() が閉じる。
	// send = true は「ここで切って送信」(止め方は同じで、親へ送信フラグを渡すだけ)
	const confirmRecording = async (send = false) => {
		if (finishing) return;

		sendOnFinish = send;
		loading = true;
		finishing = true;
		cycleAge = 0;
		clearInterval(durationCounter);

		if (usesWebSpeech()) {
			speechRecognition?.stop();
			return;
		}

		if (mediaRecorder && mediaRecorder.state === 'recording') {
			stopSegment('end');
		} else if (!stopping) {
			// 録音していない(無音待ち)= 送るものが無い。キューを drain して閉じる
			enqueueSegment([], 0, '', true);
		}
		// stopping なら onstop が finishing を見て最終セグメントとして処理する

		// 通し録音は押した時点で止める(このあとの文字起こし待ちの無音を入れない)
		stopFullRecording();
	};

	const hasTranscript = $derived(
		draftText !== '' || confirmedSegments.length > 0 || interimText !== ''
	);

	// 周期の進捗表示。idle = 発話待ち(録っていない)/ recording = 周期の目安まで /
	// pause = 目安を過ぎて息継ぎ待ち(無ければ HARD で切る)/ closing = 確定ボタン後の締め
	const cyclePhase = $derived(
		loading
			? 'closing'
			: cycleAge === 0
				? 'idle'
				: cycleAge < SEGMENT_SOFT_LIMIT
					? 'recording'
					: 'pause'
	);
	const cycleRatio = $derived(Math.min(1, cycleAge / SEGMENT_HARD_LIMIT));
	const cycleLabel = $derived(
		{
			closing: '',
			idle: $i18n.t('Waiting for speech'),
			recording: $i18n.t('Listening — text is added at the next cut'),
			pause: $i18n.t('Cutting at the next pause')
		}[cyclePhase]
	);

	let resizeObserver: ResizeObserver | undefined;

	onMount(() => {
		// listen to width changes
		resizeObserver = new ResizeObserver(() => {
			VISUALIZER_BUFFER_LENGTH = Math.floor(window.innerWidth / 4);
			if (visualizerData.length > VISUALIZER_BUFFER_LENGTH) {
				visualizerData = visualizerData.slice(visualizerData.length - VISUALIZER_BUFFER_LENGTH);
			} else {
				visualizerData = Array(VISUALIZER_BUFFER_LENGTH - visualizerData.length)
					.fill(0)
					.concat(visualizerData);
			}
		});

		resizeObserver.observe(document.body);
	});

	onDestroy(() => {
		// remove resize observer
		resizeObserver?.disconnect();

		// recording を false にする経路は親の onCancel / onConfirm だけなので、ルート遷移で
		// コンポーネントごと破棄されると recording は true のまま残る。そのままだと
		// マイクのトラックも AudioContext も duration の interval も解放されず、
		// rAF ループが回り続けて無音の切れ目ごとに転写を投げ続ける。
		// stopRecording() は再入しても安全で、aborted 経由で rAF ループと
		// 飛行中の転写(startSegment / enqueueSegment)も止まる
		destroyed = true;
		stopRecording();
	});
</script>

<div
	class="{loading
		? ' bg-gray-100/50 dark:bg-gray-850/50'
		: 'bg-indigo-300/10 dark:bg-indigo-500/10 '} {showsCycle || hasTranscript
		? 'rounded-2xl'
		: 'rounded-full'} {className}"
>
	{#if hasTranscript}
		<!-- 録音中は入力欄がこのバーに置き換わるので、読み取ったテキストをここに出す。
		     先頭に書きかけ(薄色)、続けてこの録音で確定した分(通常の色。足された瞬間だけ
		     軽く強調)— 「書きかけの末尾に足される」ことが見て分かるようにする -->
		<div
			bind:this={transcriptElement}
			class="text-sm text-left leading-relaxed text-content whitespace-pre-wrap max-h-32 overflow-y-auto px-1.5 pb-1.5"
		>
			<span class="text-content-muted" data-voice-draft>{draftText}</span
			>{#each confirmedSegments as segment, index (index)}<span
					class="voice-segment-added rounded-sm"
					data-voice-segment>{segment}</span
				>{/each}<span class="text-content-muted">{interimText}</span>
		</div>
	{/if}

	{#if showsCycle}
		<!-- 周期の進捗: バーは HARD までの長さで、目盛りが SOFT(周期の目安)。目盛りを過ぎたら
		     色を変えて「息継ぎ待ち」を示す。右に、切った分の文字起こしが走っていること(件数) -->
		<div
			class="flex items-center gap-2 px-1.5 pb-2 text-xs text-content-muted"
			data-voice-cycle={cyclePhase}
		>
			<div
				class="relative flex-1 h-1.5 rounded-full bg-indigo-500/15 overflow-hidden"
				role="progressbar"
				aria-label={cycleLabel}
				aria-valuemin={0}
				aria-valuemax={100}
				aria-valuenow={Math.round(cycleRatio * 100)}
			>
				<div
					class="h-full rounded-full {cyclePhase === 'pause'
						? 'bg-amber-500 animate-pulse'
						: 'bg-indigo-500 dark:bg-indigo-400'}"
					style="width: {cycleRatio * 100}%;"
				></div>
				<div
					class="absolute inset-y-0 w-px bg-indigo-500/60"
					style="left: {(SEGMENT_SOFT_LIMIT / SEGMENT_HARD_LIMIT) * 100}%;"
				></div>
			</div>

			<div class="shrink-0" data-voice-cycle-label>{cycleLabel}</div>

			{#if pendingSegments > 0}
				<div class="shrink-0 flex items-center gap-1 text-content" data-voice-pending>
					<span class="i-lucide-loader-circle size-3.5 block animate-spin"></span>
					{pendingSegments > 1
						? $i18n.t('Transcribing ({{n}} queued)', { n: pendingSegments })
						: $i18n.t('Transcribing')}
				</div>
			{/if}

			{#if saving}
				<div class="shrink-0 flex items-center gap-1 text-content" data-voice-saving>
					<span class="i-lucide-loader-circle size-3.5 block animate-spin"></span>
					{$i18n.t('Attaching the recording')}
				</div>
			{/if}
		</div>
	{/if}

	<div class="flex justify-between">
		<div class="flex items-center mr-1">
			<button
				type="button"
				class="p-1.5

            {loading
					? ' bg-gray-200 dark:bg-gray-700/50'
					: 'bg-indigo-400/20 text-indigo-600 dark:text-indigo-300 '}


             rounded-full"
				aria-label={$i18n.t('Cancel')}
				onclick={async () => {
					cancelled = true;
					stopRecording();
					onCancel();
				}}
			>
				<span class="i-lucide-x size-4 block"></span>
			</button>
		</div>

		<div
			class="flex flex-1 self-center items-center justify-between ml-2 mx-1 overflow-hidden h-6"
			dir="rtl"
		>
			<div
				class="flex items-center gap-0.5 h-6 w-full max-w-full overflow-hidden overflow-x-hidden flex-wrap"
			>
				{#each visualizerData.slice().reverse() as rms}
					<div class="flex items-center h-full">
						<div
							class="w-[2px] shrink-0

                    {loading
								? ' bg-gray-500 dark:bg-gray-400   '
								: 'bg-indigo-500 dark:bg-indigo-400  '}

                    inline-block h-full"
							style="height: {Math.min(100, Math.max(14, rms * 100))}%;"
						></div>
					</div>
				{/each}
			</div>
		</div>

		<div class="flex">
			<div class="  mx-1.5 pr-1 flex justify-center items-center">
				<div
					class="text-sm


        {loading ? ' text-content-muted  ' : ' text-indigo-400 '}
       font-medium flex-1 mx-auto text-center"
				>
					{formatSeconds(durationSeconds)}
				</div>
			</div>

			<div class="flex items-center">
				{#if loading}
					<div class=" text-content-muted rounded-full cursor-not-allowed">
						<span class="i-lucide-loader-circle size-6 block animate-spin"></span>
					</div>
				{:else}
					<div class="flex items-center gap-1.5">
						<Tooltip content={$i18n.t('Stop and put into the input box')}>
							<button
								type="button"
								class="p-1.5 bg-indigo-500 text-white dark:bg-indigo-500 dark:text-blue-950 rounded-full"
								aria-label={$i18n.t('Stop and put into the input box')}
								onclick={async () => {
									await confirmRecording();
								}}
							>
								<span class="i-lucide-check size-4 block"></span>
							</button>
						</Tooltip>

						<!-- ここで切ってそのまま送信(入力欄の送信ボタンと同じ見た目) -->
						<Tooltip content={$i18n.t('Stop and send')}>
							<button
								type="button"
								class="p-1.5 bg-primary hover:bg-primary-hover text-primary-content rounded-full"
								aria-label={$i18n.t('Stop and send')}
								onclick={async () => {
									await confirmRecording(true);
								}}
							>
								<span class="i-lucide-arrow-up size-4 block"></span>
							</button>
						</Tooltip>
					</div>
				{/if}
			</div>
		</div>
	</div>
</div>

<style>
	/* 確定テキストが足された瞬間の軽い強調(要素のマウント時に 1 回だけ流れる) */
	.voice-segment-added {
		animation: voice-segment-added 1.6s ease-out;
	}

	@keyframes voice-segment-added {
		from {
			background-color: rgb(99 102 241 / 0.2);
		}
		to {
			background-color: transparent;
		}
	}
</style>
