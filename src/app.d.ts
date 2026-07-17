// See https://kit.svelte.dev/docs/types#app
// for information about these interfaces
declare global {
	// vite define(vite.config.ts)で焼き込まれるビルド時定数
	const __APP_VERSION__: string;

	interface Window {
		// テーマ適用フック(存在チェック付きで呼ばれる — 未定義環境あり)
		applyTheme?: () => void;
		// Electron デスクトップラッパーが注入する IPC ブリッジ
		electronAPI?: {
			send: (payload: { type: string; data?: unknown }) => Promise<unknown>;
			load: (page: string) => void;
		};
	}

	namespace App {
		// interface Error {}
		// interface Locals {}
		// interface PageData {}
		// interface Platform {}
	}
}

export {};
