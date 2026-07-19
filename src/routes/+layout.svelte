<script>
	// フォントはビルド時にバンドル(CDN 不使用 — オフライン完結)。
	// sans = Inter + Noto Sans JP / mono = JetBrains Mono + M PLUS 1 Code(等幅日本語)。
	import '@fontsource-variable/inter';
	import '@fontsource-variable/noto-sans-jp';
	import '@fontsource-variable/jetbrains-mono';
	import '@fontsource-variable/m-plus-1-code';

	import { onMount, tick, setContext } from 'svelte';
	import {
		config,
		user,
		settings,
		theme,
		WEBUI_NAME,
		mobile,
		chats,
		currentChatPage,
		isLastActiveTab,
		isApp,
		appInfo,
		appData,
		backendUnreachable
	} from '$lib/stores';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { Toaster, toast } from 'svelte-sonner';

	import { getBackendConfig } from '$lib/apis';
	import { SESSION_EXPIRED_FLAG } from '$lib/apis/fetch';
	import { getSessionUser } from '$lib/apis/auths';
	import { setFallbackHandler } from '$lib/utils/completion-streams';

	import '../tailwind.css';
	import '../app.css';

	import 'tippy.js/dist/tippy.css';

	import { WEBUI_BASE_URL, WEBUI_HOSTNAME } from '$lib/constants';
	import i18n, { initI18n, getLanguages, changeLanguage } from '$lib/i18n';
	import { bestMatchingLanguage } from '$lib/utils';
	import { getChatList } from '$lib/apis/chats';
	import NotificationToast from '$lib/components/NotificationToast.svelte';
	import AppSidebar from '$lib/components/app/AppSidebar.svelte';

	setContext('i18n', i18n);

	const bc = new BroadcastChannel('active-tab-channel');

	let { children } = $props();

	let loaded = $state(false);

	const BREAKPOINT = 768;

	// llens(wire v2): 表示中でないチャット(Chat コンポーネントがハンドラ未登録)の
	// completion イベント既定処理。done の通知トーストとチャット一覧の追従だけ担う。
	/** @param {import('$lib/utils/completion-streams').CompletionEnvelope} envelope */
	const fallbackChatEventHandler = async (envelope) => {
		const type = envelope?.data?.type ?? null;
		const data = envelope?.data?.data ?? null;

		if (type === 'chat:completion') {
			const { done, content, title } = data ?? {};

			if (done) {
				if ($isLastActiveTab) {
					if ($settings?.notificationEnabled ?? false) {
						new Notification(`${title} | ${$WEBUI_NAME}`, {
							body: content,
							icon: `${WEBUI_BASE_URL}/static/favicon.png`
						});
					}
				}

				// NotificationToast は dispatcher を持たない(#70 A2-2)。クリック時の
				// トースト消去は id を捕まえて toast.dismiss で行う。
				// runes 化した NotificationToast は関数型 Component だが、svelte-sonner 0.3 の
				// 型は旧 ComponentType を要求する(実行時は svelte:component で動く)。
				const toastId = toast.custom(/** @type {any} */ (NotificationToast), {
					componentProps: {
						onClick: () => {
							goto(`/c/${envelope.chat_id}`);
							toast.dismiss(toastId);
						},
						content: content,
						title: title
					},
					duration: 15000,
					unstyled: true
				});
			}
		} else if (type === 'chat:title') {
			currentChatPage.set(1);
			await chats.set(await getChatList($currentChatPage));
		}
	};

	setFallbackHandler(fallbackChatEventHandler);

	onMount(async () => {
		if (typeof window !== 'undefined' && window.applyTheme) {
			window.applyTheme();
		}

		if (window?.electronAPI) {
			const info = await window.electronAPI.send({
				type: 'app:info'
			});

			if (info) {
				isApp.set(true);
				appInfo.set(info);

				const data = await window.electronAPI.send({
					type: 'app:data'
				});

				if (data) {
					appData.set(data);
				}
			}
		}

		// Listen for messages on the BroadcastChannel
		bc.onmessage = (event) => {
			if (event.data === 'active') {
				isLastActiveTab.set(false); // Another tab became active
			}
		};

		// Set yourself as the last active tab when this tab is focused
		const handleVisibilityChange = () => {
			if (document.visibilityState === 'visible') {
				isLastActiveTab.set(true); // This tab is now the active tab
				bc.postMessage('active'); // Notify other tabs that this tab is active
			}
		};

		// Add event listener for visibility state changes
		document.addEventListener('visibilitychange', handleVisibilityChange);

		// Call visibility change handler initially to set state on load
		handleVisibilityChange();

		theme.set(localStorage.theme);

		mobile.set(window.innerWidth < BREAKPOINT);

		const onResize = () => {
			if (window.innerWidth < BREAKPOINT) {
				mobile.set(true);
			} else {
				mobile.set(false);
			}
		};
		window.addEventListener('resize', onResize);

		let backendConfig = null;
		try {
			backendConfig = await getBackendConfig();
			console.log('Backend config:', backendConfig);
		} catch (error) {
			console.error('Error loading backend config:', error);
		}
		// Initialize i18n even if we didn't get a backend config,
		// so `/error` can show something that's not `undefined`.

		initI18n(localStorage?.locale);
		if (!localStorage.locale) {
			const languages = await getLanguages();
			const browserLanguages = navigator.languages ? navigator.languages : [navigator.language];
			const lang = backendConfig.default_locale
				? backendConfig.default_locale
				: bestMatchingLanguage(languages, browserLanguages, 'en-US');
			changeLanguage(lang);
		}

		if (backendConfig) {
			// Save Backend Status to Store
			await config.set(backendConfig);
			await WEBUI_NAME.set(backendConfig.name);

			if ($config) {
				const currentUrl = `${window.location.pathname}${window.location.search}`;
				const encodedUrl = encodeURIComponent(currentUrl);

				// llens(#70 B1): セッションは httpOnly cookie に一本化 — token の有無を JS から
				// 判定できないので、常に /auths/(me)を叩いて cookie セッションの生死で分岐する
				const sessionUser = await getSessionUser().catch(() => null);

				if (sessionUser) {
					// Save Session User to Store
					await user.set(sessionUser);
					await config.set(await getBackendConfig());
				} else {
					// 未ログイン or セッション失効。旧 localStorage token は移行残骸として掃除
					localStorage.removeItem('token');
					if ($page.url.pathname !== '/auth') {
						await goto(`/auth?redirect=${encodedUrl}`);
					}
				}
			}
		} else {
			// Redirect to /error when Backend Not Detected
			await goto(`/error`);
		}

		await tick();

		document.getElementById('splash-screen')?.remove();
		loaded = true;

		// llens(#70 B2): apiFetch がセッション失効を検知してハード遷移してきた場合の通知。
		// 子ページの onMount 中に toast を撃つと Toaster マウント前後の競合で落ちるため、
		// 最後に走る root layout の onMount 末尾(loaded 確定後)で発火する。
		if (sessionStorage.getItem(SESSION_EXPIRED_FLAG)) {
			sessionStorage.removeItem(SESSION_EXPIRED_FLAG);
			// Toaster のマウント時 reset() を跨がないよう一拍置く。見逃しやすい通知なので長め(8s)
			setTimeout(() => {
				toast.info($i18n.t('Your session has expired. Please sign in again.'), {
					duration: 8000
				});
			}, 500);
		}

		// NOTE: async onMount の戻り値クリーンアップは Svelte が無視する(同期関数限定)。
		// root layout はアンマウントされないため onResize の解除は不要 — 死にコードだった
		// return を型付けに合わせて撤去(挙動不変)。
	});
</script>

<svelte:head>
	<title>{$WEBUI_NAME}</title>
	<link crossorigin="anonymous" rel="icon" href="{WEBUI_BASE_URL}/static/favicon.png" />

	<!-- rosepine themes have been disabled as it's not up to date with our latest version. -->
	<!-- feel free to make a PR to fix if anyone wants to see it return -->
	<!-- <link rel="stylesheet" type="text/css" href="/themes/rosepine.css" />
	<link rel="stylesheet" type="text/css" href="/themes/rosepine-dawn.css" /> -->
</svelte:head>

{#if $backendUnreachable}
	<!-- llens(#70 B3): バックエンド不達バナー。apiFetch が検知し /health ポーリングで自動復帰 -->
	<div
		class="fixed top-0 inset-x-0 z-9999 bg-red-600 text-white text-xs text-center py-1.5 font-medium"
		role="alert"
	>
		{$i18n.t('Cannot connect to the server. Retrying...')}
	</div>
{/if}

{#if loaded}
	{#if $isApp}
		<div class="flex flex-row h-screen">
			<AppSidebar />

			<div class="w-full flex-1 max-w-[calc(100%-4.5rem)]">
				{@render children?.()}
			</div>
		</div>
	{:else}
		{@render children?.()}
	{/if}
{/if}

<Toaster
	theme={$theme.includes('dark')
		? 'dark'
		: $theme === 'system'
			? window.matchMedia('(prefers-color-scheme: dark)').matches
				? 'dark'
				: 'light'
			: 'light'}
	richColors
	position="bottom-right"
/>
