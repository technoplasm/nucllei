import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
	// Consult https://kit.svelte.dev/docs/integrations#preprocessors
	// for more information about preprocessors
	preprocess: vitePreprocess(),
	kit: {
		// adapter-auto only supports some environments, see https://kit.svelte.dev/docs/adapter-auto for a list.
		// If your environment is not supported or you settled on a specific environment, switch out the adapter.
		// See https://kit.svelte.dev/docs/adapters for more information about adapters.
		adapter: adapter({
			pages: 'build',
			assets: 'build',
			fallback: 'index.html'
		})
	},
	vitePlugin: {
		// inspector: {
		// 	toggleKeyCombo: 'meta-shift', // Key combination to open the inspector
		// 	holdMode: false, // Enable or disable hold mode
		// 	showToggleButton: 'always', // Show toggle button ('always', 'active', 'never')
		// 	toggleButtonPos: 'bottom-right' // Position of the toggle button
		// }
	},
	onwarn: (warning, handler) => {
		const { code } = warning;
		if (code === 'css-unused-selector' || code === 'css_unused_selector') return;
		// llens(#70 A2): 行クリック等のポインタ補助に対する a11y 警告はミュート
		// (キーボード経路は aria-label 付きの明示ボタンが担保する方針)。
		// svelte-check 側は package.json の --compiler-warnings に同じ設定がある。
		if (
			code === 'a11y_no_noninteractive_element_interactions' ||
			code === 'a11y_click_events_have_key_events'
		)
			return;

		handler(warning);
	}
};

export default config;
