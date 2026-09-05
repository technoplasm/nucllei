import { type Writable, writable } from 'svelte/store';
import type { ModelConfig } from '$lib/apis';
import type { Banner } from '$lib/types';
import type { KokoroWorker } from '$lib/workers/KokoroWorker';

// Backend
// 配備層のブランド名。/api/config の name(WEBUI_NAME env、既定 Nucllei)で上書きされる
export const WEBUI_NAME = writable('Nucllei');
export const config: Writable<Config | undefined> = writable(undefined);
export const user: Writable<SessionUser | undefined> = writable(undefined);

// Electron App
export const isApp = writable(false);
// Electron ブリッジ(app:info / app:data)の戻りをそのまま保持する(読み手は現状なし)
export const appInfo: Writable<unknown> = writable(null);
export const appData: Writable<unknown> = writable(null);

// Frontend
export const MODEL_DOWNLOAD_POOL = writable<Record<string, any>>({});

export const mobile = writable(false);

export const theme = writable('system');

export const TTSWorker: Writable<KokoroWorker | null> = writable(null);

export const chatId = writable('');
export const chatTitle = writable('');

// チャット一覧(サイドバー)の項目。API(/chats/ 系 list)の戻りに
// フロントで time_range を付与したもの — 一覧表示に使う最小型。
export type ChatListItem = {
	id: string;
	title: string;
	updated_at: number;
	created_at: number;
	time_range?: string;
	pinned?: boolean;
};

export const chats: Writable<ChatListItem[] | null> = writable(null);
export const pinnedChats: Writable<ChatListItem[]> = writable([]);

export const models: Writable<Model[]> = writable([]);

export const skills: Writable<null | Skill[]> = writable(null);
export const tools = writable(null);
export const functions = writable(null);

export const banners: Writable<Banner[]> = writable([]);

export const settings: Writable<Settings> = writable({});

export const showSidebar = writable(false);
export const showSettings = writable(false);
export const showArchivedChats = writable(false);

export const showControls = writable(false);
export const showCallOverlay = writable(false);

export const scrollPaginationEnabled = writable(false);
export const currentChatPage = writable(1);

export const isLastActiveTab = writable(true);
export const playingNotificationSound = writable(false);

// llens(#70 B3): バックエンド不達(network error / 502-504)の可視化。
// $lib/apis/fetch.ts の apiFetch が設定し、root layout がバナー表示する。
export const backendUnreachable = writable(false);

export type Model = OpenAIModel | OllamaModel;

type BaseModel = {
	id: string;
	name: string;
	info?: ModelConfig;
	owned_by: 'ollama' | 'openai';
};

export interface OpenAIModel extends BaseModel {
	owned_by: 'openai';
	external: boolean;
	source?: string;
}

export interface OllamaModel extends BaseModel {
	owned_by: 'ollama';
	details: OllamaModelDetails;
	size: number;
	description: string;
	model: string;
	modified_at: string;
	digest: string;
	ollama?: {
		name?: string;
		model?: string;
		modified_at: string;
		size?: number;
		digest?: string;
		details?: {
			parent_model?: string;
			format?: string;
			family?: string;
			families?: string[];
			parameter_size?: string;
			quantization_level?: string;
		};
		urls?: number[];
	};
}

type OllamaModelDetails = {
	parent_model: string;
	format: string;
	family: string;
	families: string[] | null;
	parameter_size: string;
	quantization_level: string;
};

type Settings = {
	models?: string[];
	conversationMode?: boolean;
	speechAutoSend?: boolean;
	responseAutoPlayback?: boolean;
	audio?: AudioSettings;
	showUsername?: boolean;
	notificationEnabled?: boolean;
	title?: TitleSettings;
	splitLargeDeltas?: boolean;
	// optional: settings store の初期値は {}(消費側は ?? 'auto' で既定化)
	chatDirection?: 'LTR' | 'RTL' | 'auto';
	ctrlEnterToSend?: boolean;

	// llens(#70): SettingsModal > Interface が実際に保存しているキーを型に反映
	titleAutoGenerate?: boolean;
	callEnabled?: boolean;
	widescreenMode?: boolean;
	splitLargeChunks?: boolean;
	scrollOnBranchChange?: boolean;
	notificationSound?: boolean;
	landingPageMode?: string;
	chatBubble?: boolean;
	copyFormatted?: boolean;
	expandCodeBlocks?: boolean;
	expandDetails?: boolean;
	voiceInterruption?: boolean;
	// llens(#56): 音声入力の共通パラメータ。Call オーバーレイと逐次ディクテーション(録音バー)の
	// 両方が読む。既定値は各消費側の定数(4000 / 1000ms)— 根拠は _memos/audio.md「読み取り猶予」節
	voiceSilenceTimeout?: number; // 無音がこの ms 続いたら発話の区切りとみなす(読み取り猶予)
	voiceMinChunkDuration?: number; // これより短い録音チャンクは文字起こしへ送らない(ms)
	hapticFeedback?: boolean;
	backgroundImageUrl?: string | null;
	notifications?: { webhook_url?: string };

	// llens(#70 C): per-user 既定の chat params(Chat.svelte が chat 側 params とマージして送信)
	params?: {
		stream_response?: boolean;
		stop?: string | string[];
		[key: string]: unknown;
	};

	system?: string;
	requestFormat?: string;
	keepAlive?: string;
	seed?: number;
	temperature?: string;
	repeat_penalty?: string;
	top_k?: string;
	top_p?: string;
	num_ctx?: string;
	num_batch?: string;
	num_keep?: string;
	options?: ModelOptions;
};

type ModelOptions = {
	stop?: boolean;
};

type AudioSettings = {
	STTEngine?: string;
	TTSEngine?: string;
	speaker?: string;
	model?: string;
	nonLocalVoices?: boolean;
	// llens(#70): 実際に使われている形(Settings > Audio が保存する nested 形)を型に反映
	stt?: {
		engine?: string;
	};
	tts?: {
		engine?: string;
		voice?: string;
		defaultVoice?: string;
		playbackRate?: number;
		nonLocalVoices?: boolean;
		engineConfig?: {
			dtype?: string;
		};
	};
};

type TitleSettings = {
	auto?: boolean;
	model?: string;
	modelExternal?: string;
	prompt?: string;
};

// llens(#43): 可読キーは id 一本(旧 command は廃止)・表示名は name(旧 title)。
// 形は nucllei/models/skills.py SkillModel(getSkills の返却)に一致させる。
export type Skill = {
	id: string;
	user_id: string;
	name: string;
	description?: string | null;
	content: string;
	tool_ids: string[];
	access_control?: null | object;
	created_at: number;
	updated_at: number;
};

// llens(#66/#70): backend USER_PERMISSIONS(3層権限)の形。グループ内は boolean フラグの束
type UserPermissions = {
	workspace?: {
		skills?: boolean;
		[key: string]: boolean | undefined;
	};
	chat?: {
		[key: string]: boolean | undefined;
	};
};

type Config = {
	status: boolean;
	name: string;
	// 配備層(WEBUI_VERSION / WEBUI_COPYRIGHT env。空なら License タブで非表示)
	webui_version?: string;
	copyright?: string;
	version: string;
	default_locale: string;
	// 共有 PC のアイドルタイムアウト秒数(= 既存 JWT_EXPIRES_IN。無期限なら null)。
	// FE 側タイマ($lib/apis/fetch の startIdleWatch)の TTL — 設定項目ではない
	session_idle_timeout?: number | null;
	default_models: string;
	default_prompt_suggestions: PromptSuggestion[];
	features: {
		auth: boolean;
		auth_trusted_header: boolean;
		enable_api_key: boolean;
		enable_signup: boolean;
		enable_login_form: boolean;
		// 共有 PC 配備で false。ログインフォームの autocomplete 属性を抑止方向に切り替える
		// (ブラウザの保存プロンプト自体は止められない — _memos/auth-profile.md 参照)
		enable_login_autocomplete?: boolean;
		enable_admin_export: boolean;
		enable_admin_chat_access: boolean;
		enable_user_webhooks?: boolean;
		enable_ldap?: boolean;
	};
	oauth: {
		providers: {
			[key: string]: string;
		};
	};
	// llens(#70): /api/config が user 認証時に返す追加フィールド(main.py 参照)を型に反映
	audio?: {
		tts: {
			// 配備レベルの Read Aloud 有効化(ENABLE_AUDIO_TTS — nucllei/main.py)
			enable?: boolean;
			engine: string;
			voice: string;
			split_on?: string;
		};
		stt: {
			// 配備レベルの音声入力有効化(ENABLE_AUDIO_STT — nucllei/main.py)
			enable?: boolean;
			engine: string;
		};
	};
	file?: {
		max_size?: number | null;
		max_count?: number | null;
		allowed_extensions?: string[];
		image_max_dimension?: number | null;
		pasted_text_character_limit?: number;
	};
	permissions?: UserPermissions;
};

type PromptSuggestion = {
	content: string;
	title: [string, string];
};

export type SessionUser = {
	id: string;
	email: string;
	name: string;
	role: string;
	profile_image_url: string;
	// llens(#66/#70): get_session_user が返す実効権限(admin は enforcement 側でバイパス)
	permissions?: UserPermissions;
	token?: string;
	token_type?: string;
	expires_at?: number | null;
};
