import { theme } from '$lib/stores';

// 適用対象として認識するテーマ一覧(クラス除去の際に使う)
export const THEMES = ['dark', 'light'];

// テーマを DOM に適用する(store/localStorage は変更しない — themeChangeHandler が担当)。
// 元は Settings/General.svelte にあったロジックの一本化。app.html の起動時スクリプトと対を成す。
export const applyTheme = (_theme: string) => {
	let themeToApply = _theme;

	if (_theme === 'system') {
		themeToApply = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
	}

	if (themeToApply === 'dark') {
		document.documentElement.style.setProperty('--color-gray-800', '#333');
		document.documentElement.style.setProperty('--color-gray-850', '#262626');
		document.documentElement.style.setProperty('--color-gray-900', '#171717');
		document.documentElement.style.setProperty('--color-gray-950', '#0d0d0d');
	}

	THEMES.filter((e) => e !== themeToApply).forEach((e) => {
		document.documentElement.classList.remove(e);
	});

	document.documentElement.classList.add(themeToApply);

	const metaThemeColor = document.querySelector('meta[name="theme-color"]');
	if (metaThemeColor) {
		metaThemeColor.setAttribute('content', themeToApply === 'dark' ? '#171717' : '#ffffff');
	}

	if (typeof window !== 'undefined' && (window as any).applyTheme) {
		(window as any).applyTheme();
	}
};

// テーマを切り替えて永続化する(store + localStorage + DOM 適用)。
export const themeChangeHandler = (_theme: string) => {
	theme.set(_theme);
	localStorage.setItem('theme', _theme);
	applyTheme(_theme);
};
