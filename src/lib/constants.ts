import { browser } from '$app/environment';
// import { version } from '../../package.json';

// llens(dev): dev も本番(docker = 単一オリジンで BE が SPA と API を両方配信)と同じく
// 相対 URL で叩く。vite proxy(vite.config.ts)が /api・/openai・/ollama・/ws・/oauth を
// BE(:3000)へ流すので、フロントは同一オリジンのまま。画像等の cookie 認証もこれで通る
// (旧: dev だけ http://host:8080 へクロスオリジン直叩き → 画像が cookie 不達で出ない罠)。
export const WEBUI_HOSTNAME = browser ? location.hostname : '';
export const WEBUI_BASE_URL = ``;
export const WEBUI_API_BASE_URL = `${WEBUI_BASE_URL}/api/v1`;

export const OLLAMA_API_BASE_URL = `${WEBUI_BASE_URL}/ollama`;
export const OPENAI_API_BASE_URL = `${WEBUI_BASE_URL}/openai`;
export const AUDIO_API_BASE_URL = `${WEBUI_BASE_URL}/api/v1/audio`;
export const IMAGES_API_BASE_URL = `${WEBUI_BASE_URL}/api/v1/images`;

// 本体(Nucllei)のバージョン(vite define でビルド時に焼き込み)。
// 配備層(WEBUI_NAME/WEBUI_VERSION/WEBUI_COPYRIGHT)は /api/config が返す。
export const APP_VERSION = __APP_VERSION__;
export const REQUIRED_OLLAMA_VERSION = '0.1.16';

// カスタムアイコン未設定のモデルに使う既定アバター(gray 版プリズム)。
// ブランドロゴ(brand.png / favicon.png = カラー)とは別。
// 旧 favicon-dark.png から意味名 model.png へ(#84)— CUSTOM_DIR で配備先が差し替え可能。
export const DEFAULT_MODEL_IMAGE_URL = '/static/model.png';

export const SUPPORTED_FILE_TYPE = [
	'application/epub+zip',
	'application/pdf',
	'text/plain',
	'text/csv',
	'text/xml',
	'text/html',
	'text/x-python',
	'text/css',
	'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
	'application/octet-stream',
	'application/x-javascript',
	'text/markdown',
	'audio/mpeg',
	'audio/wav',
	'audio/ogg',
	'audio/x-m4a'
];

export const SUPPORTED_FILE_EXTENSIONS = [
	'md',
	'rst',
	'go',
	'py',
	'java',
	'sh',
	'bat',
	'ps1',
	'cmd',
	'js',
	'ts',
	'css',
	'cpp',
	'hpp',
	'h',
	'c',
	'cs',
	'htm',
	'html',
	'sql',
	'log',
	'ini',
	'pl',
	'pm',
	'r',
	'dart',
	'dockerfile',
	'env',
	'php',
	'hs',
	'hsc',
	'lua',
	'nginxconf',
	'conf',
	'm',
	'mm',
	'plsql',
	'perl',
	'rb',
	'rs',
	'db2',
	'scala',
	'bash',
	'swift',
	'vue',
	'svelte',
	'doc',
	'docx',
	'pdf',
	'csv',
	'txt',
	'xls',
	'xlsx',
	'pptx',
	'ppt',
	'msg'
];

// Source: https://kit.svelte.dev/docs/modules#$env-static-public
// This feature, akin to $env/static/private, exclusively incorporates environment variables
// that are prefixed with config.kit.env.publicPrefix (usually set to PUBLIC_).
// Consequently, these variables can be securely exposed to client-side code.
