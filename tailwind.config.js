import typography from '@tailwindcss/typography';
import containerQuries from '@tailwindcss/container-queries';
import { iconsPlugin, getIconCollections } from '@egoist/tailwindcss-icons';

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
					raised: 'var(--surface-raised, #ffffff)',
					// hover
					hover: 'var(--surface-hover, #fafafa)',
					// 控えめな帯(table の thead など)。raised の上に置いて区別が付く濃さ。
					muted: 'var(--surface-muted, #f0f0f0)'
				},
				// サイドバー行のインタラクション色(hover / 選択中)。実値は src/tailwind.css。
				sidebar: {
					hover: 'var(--sidebar-hover, #ececec)',
					selected: 'var(--sidebar-selected, #e3e3e3)'
				},
				// 罫線・区切り線の共通色(テーブル/hr など)。実値は src/tailwind.css。border-line で使う
				line: 'var(--line, #dcdcdc)',
				// 主ボタン(Save/Create 等)。現行は黒/白反転のモノクロ —
				// ブランディング確定(#58)時は src/tailwind.css の値だけ変えればよい。
				// 使い方: bg-primary hover:bg-primary-hover text-primary-content(dark: 変種不要)
				primary: {
					DEFAULT: 'var(--primary, #171717)',
					hover: 'var(--primary-hover, #333333)',
					content: 'var(--primary-content, #ffffff)'
				},
				// 副ボタン(戻る/キャンセル等)。raised(入力面)とは役割を分ける
				secondary: {
					DEFAULT: 'var(--secondary, #e9e9e9)',
					hover: 'var(--secondary-hover, #dddddd)',
					content: 'var(--secondary-content, #333333)'
				},
				// テキスト(前景)の意味トークン。surface と対(surface=背景 / content=前景)。
				// light/dark の実値は src/tailwind.css で gray スケールに乗せて一括管理(dark: 変種は不要)。
				//   content        … 本文(既定のテキスト色)
				//   content-muted  … 2次情報・ラベル・キャプション
				//   content-subtle … placeholder・disabled・3次(最も控えめ)
				content: {
					DEFAULT: 'var(--content, #333333)',
					muted: 'var(--content-muted, #9b9b9b)',
					subtle: 'var(--content-subtle, #b4b4b4)'
				}
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
	plugins: [
		typography,
		containerQuries,
		// llens(#70): class ベースアイコン(i-lucide-*)。CSS mask で currentColor 継承、
		// 使ったクラスだけ emit(ビルド時生成、ランタイム外部依存なし)。動的なクラス名合成は禁止。
		iconsPlugin({ collections: getIconCollections(['lucide']) })
	]
};
