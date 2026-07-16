import { type Writable, writable } from 'svelte/store';
import type { ModelConfig } from '$lib/apis';
import type { Banner } from '$lib/types';
import type { Socket } from 'socket.io-client';

// Backend
// 配備層のブランド名。/api/config の name(WEBUI_NAME env、既定 Nucllei)で上書きされる
export const WEBUI_NAME = writable('Nucllei');
export const config: Writable<Config | undefined> = writable(undefined);
export const user: Writable<SessionUser | undefined> = writable(undefined);

// Electron App
export const isApp = writable(false);
export const appInfo = writable(null);
export const appData = writable(null);

// Frontend
export const MODEL_DOWNLOAD_POOL = writable<Record<string, any>>({});

export const mobile = writable(false);

export const socket: Writable<null | Socket> = writable(null);
export const activeUserIds: Writable<null | string[]> = writable(null);
export const USAGE_POOL: Writable<null | string[]> = writable(null);

export const theme = writable('system');

export const TTSWorker = writable(null);

export const chatId = writable('');
export const chatTitle = writable('');

export const chats = writable(null);
export const pinnedChats = writable([]);

export const models: Writable<Model[]> = writable([]);

export const skills: Writable<null | Skill[]> = writable(null);
export const tools = writable(null);
export const functions = writable(null);

export const banners: Writable<Banner[]> = writable([]);

export const settings: Writable<Settings> = writable({});

export const showSidebar = writable(false);
export const showSettings = writable(false);
export const showArchivedChats = writable(false);
export const showChangelog = writable(false);

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
	responseAutoCopy?: boolean;
	widescreenMode?: boolean;
	splitLargeChunks?: boolean;
	scrollOnBranchChange?: boolean;
	notificationSound?: boolean;
	richTextInput?: boolean;
	largeTextAsFile?: boolean;
	landingPageMode?: string;
	chatBubble?: boolean;
	copyFormatted?: boolean;
	collapseCodeBlocks?: boolean;
	expandDetails?: boolean;
	imageCompression?: boolean;
	imageCompressionSize?: { width: string; height: string };
	showUpdateToast?: boolean;
	showChangelog?: boolean;
	voiceInterruption?: boolean;
	hapticFeedback?: boolean;
	iframeSandboxAllowSameOrigin?: boolean;
	iframeSandboxAllowForms?: boolean;
	backgroundImageUrl?: string | null;
	notifications?: { webhook_url?: string };

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

type Skill = {
	id: string;
	command: string;
	user_id: string;
	title: string;
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
	features?: {
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
	default_models: string;
	default_prompt_suggestions: PromptSuggestion[];
	features: {
		auth: boolean;
		auth_trusted_header: boolean;
		enable_api_key: boolean;
		enable_signup: boolean;
		enable_login_form: boolean;
		enable_image_generation: boolean;
		enable_admin_export: boolean;
		enable_admin_chat_access: boolean;
		enable_user_webhooks?: boolean;
	};
	oauth: {
		providers: {
			[key: string]: string;
		};
	};
	// llens(#70): /api/config が user 認証時に返す追加フィールド(main.py 参照)を型に反映
	audio?: {
		tts: {
			engine: string;
			voice: string;
			split_on?: string;
		};
		stt: {
			engine: string;
		};
	};
	file?: {
		max_size?: number | null;
		max_count?: number | null;
		allowed_extensions?: string[];
	};
	permissions?: UserPermissions;
};

type PromptSuggestion = {
	content: string;
	title: [string, string];
};

type SessionUser = {
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
