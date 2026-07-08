import { theme } from '$lib/stores';

// 適用対象として認識するテーマ一覧(クラス除去の際に使う)
export const THEMES = ['dark', 'light', 'rose-pine dark', 'rose-pine-dawn light', 'oled-dark'];

// テーマを DOM に適用する(store/localStorage は変更しない — themeChangeHandler が担当)。
// 元は Settings/General.svelte にあったロジックの一本化。app.html の起動時スクリプトと対を成す。
export const applyTheme = (_theme: string) => {
	let themeToApply = _theme === 'oled-dark' ? 'dark' : _theme;

	if (_theme === 'system') {
		themeToApply = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
	}

	if (themeToApply === 'dark' && !_theme.includes('oled')) {
		document.documentElement.style.setProperty('--color-gray-800', '#333');
		document.documentElement.style.setProperty('--color-gray-850', '#262626');
		document.documentElement.style.setProperty('--color-gray-900', '#171717');
		document.documentElement.style.setProperty('--color-gray-950', '#0d0d0d');
	}

	THEMES.filter((e) => e !== themeToApply).forEach((e) => {
		e.split(' ').forEach((e) => {
			document.documentElement.classList.remove(e);
		});
	});

	themeToApply.split(' ').forEach((e) => {
		document.documentElement.classList.add(e);
	});

	const metaThemeColor = document.querySelector('meta[name="theme-color"]');
	if (metaThemeColor) {
		if (_theme.includes('system')) {
			const systemTheme = window.matchMedia('(prefers-color-scheme: dark)').matches
				? 'dark'
				: 'light';
			metaThemeColor.setAttribute('content', systemTheme === 'light' ? '#ffffff' : '#171717');
		} else {
			metaThemeColor.setAttribute(
				'content',
				_theme === 'dark'
					? '#171717'
					: _theme === 'oled-dark'
						? '#000000'
						: _theme === 'her'
							? '#983724'
							: '#ffffff'
			);
		}
	}

	if (typeof window !== 'undefined' && (window as any).applyTheme) {
		(window as any).applyTheme();
	}

	if (_theme.includes('oled')) {
		document.documentElement.style.setProperty('--color-gray-800', '#101010');
		document.documentElement.style.setProperty('--color-gray-850', '#050505');
		document.documentElement.style.setProperty('--color-gray-900', '#000000');
		document.documentElement.style.setProperty('--color-gray-950', '#000000');
		document.documentElement.classList.add('dark');
	}
};

// テーマを切り替えて永続化する(store + localStorage + DOM 適用)。
export const themeChangeHandler = (_theme: string) => {
	theme.set(_theme);
	localStorage.setItem('theme', _theme);
	applyTheme(_theme);
};
