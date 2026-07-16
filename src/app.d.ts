// See https://kit.svelte.dev/docs/types#app
// for information about these interfaces
declare global {
	// vite define(vite.config.ts)で焼き込まれるビルド時定数
	const __APP_VERSION__: string;

	namespace App {
		// interface Error {}
		// interface Locals {}
		// interface PageData {}
		// interface Platform {}
	}
}

export {};
