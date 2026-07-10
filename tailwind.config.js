import typography from '@tailwindcss/typography';
import containerQuries from '@tailwindcss/container-queries';

/** @type {import('tailwindcss').Config} */
export default {
	darkMode: 'class',
	content: ['./src/**/*.{html,js,svelte,ts}'],
	theme: {
		extend: {
			fontFamily: {
				sans: [
					'Inter Variable',
					'Noto Sans JP Variable',
					'ui-sans-serif',
					'system-ui',
					'sans-serif'
				],
				mono: ['JetBrains Mono Variable', 'M PLUS 1 Code Variable', 'ui-monospace', 'monospace']
			},
			colors: {
				gray: {
					50: 'var(--color-gray-50, #f9f9f9)',
					100: 'var(--color-gray-100, #ececec)',
					200: 'var(--color-gray-200, #e3e3e3)',
					300: 'var(--color-gray-300, #cdcdcd)',
					400: 'var(--color-gray-400, #b4b4b4)',
					500: 'var(--color-gray-500, #9b9b9b)',
					600: 'var(--color-gray-600, #676767)',
					700: 'var(--color-gray-700, #4e4e4e)',
					800: 'var(--color-gray-800, #333)',
					850: 'var(--color-gray-850, #262626)',
					900: 'var(--color-gray-900, #171717)',
					950: 'var(--color-gray-950, #0d0d0d)'
				},
				// アプリの2大サーフェス(メイン背景 / サイドバー背景)。
				// light/dark の実値は src/tailwind.css の :root / html.dark で一括調整する。
				surface: {
					DEFAULT: 'var(--surface, #f9f9f9)',
					sidebar: 'var(--surface-sidebar, #ececec)',
					// 背景から1段持ち上げる面(チャット入力欄などのカード)。サーフェス基準で馴染む。
					raised: 'var(--surface-raised, #ffffff)'
				},
				// サイドバー行のインタラクション色(hover / 選択中)。実値は src/tailwind.css。
				sidebar: {
					hover: 'var(--sidebar-hover, #ececec)',
					selected: 'var(--sidebar-selected, #e3e3e3)'
				},
				// 罫線・区切り線の共通色(テーブル/hr など)。実値は src/tailwind.css。border-line で使う
				line: 'var(--line, #dcdcdc)'
			},
			typography: {
				DEFAULT: {
					css: {
						pre: false,
						code: false,
						'pre code': false,
						'code::before': false,
						'code::after': false
					}
				}
			},
			padding: {
				'safe-bottom': 'env(safe-area-inset-bottom)'
			}
		}
	},
	plugins: [typography, containerQuries]
};
