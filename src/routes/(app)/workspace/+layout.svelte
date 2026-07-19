<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { WEBUI_NAME, showSidebar, functions, user, mobile, models, tools } from '$lib/stores';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';

	const i18n = getContext('i18n');

	let { children } = $props();

	let loaded = $state(false);

	// llens: workspace 上部メニューは宣言的に定義する。並び・ラベル・表示条件(show)はこの配列だけで
	// 管理し、マークアップは下の {#each} 一箇所。ラベルは $i18n.t(label) で翻訳する。
	// (#48 Knowledge はメニュー撤去 / #69 Files は全ユーザー / #19 Chats は admin のチャット閲覧)
	const WORKSPACE_TABS = [
		{ label: 'Models', path: 'models', show: (u: any) => u?.role === 'admin' },
		{ label: 'Files', path: 'files', show: (_u: any) => true },
		{
			label: 'Skills',
			path: 'skills',
			show: (u: any) => u?.role === 'admin' || u?.permissions?.workspace?.skills
		},
		{ label: 'Tools', path: 'tools', show: (u: any) => u?.role === 'admin' },
		{ label: 'Functions', path: 'functions', show: (u: any) => u?.role === 'admin' },
		{ label: 'Users', path: 'users', show: (u: any) => u?.role === 'admin' },
		{ label: 'Chats', path: 'chats', show: (u: any) => u?.role === 'admin' },
		{ label: 'Settings', path: 'settings', show: (u: any) => u?.role === 'admin' }
	];
	const visibleWorkspaceTabs = $derived(WORKSPACE_TABS.filter((t) => t.show($user)));

	onMount(async () => {
		if ($user?.role !== 'admin') {
			const path = $page.url.pathname;
			// llens(#66): 3層化 — Models/Tools/Functions/Settings/Users は admin 専用。
			// Skills だけ skill editor(workspace.skills)に開く。
			// Files(#69 → workspace 移設)は全ログインユーザーに開く。
			if (
				path.includes('/functions') ||
				path.includes('/settings') ||
				path.includes('/users') ||
				path.includes('/chats') ||
				path.includes('/models') ||
				path.includes('/tools')
			) {
				goto('/');
			} else if (path.includes('/skills') && !$user?.permissions?.workspace?.skills) {
				goto('/');
			}
		}

		loaded = true;
	});
</script>

<svelte:head>
	<title>
		{$i18n.t('Workspace')} | {$WEBUI_NAME}
	</title>
</svelte:head>

{#if loaded}
	<div
		class=" relative flex flex-col w-full h-screen max-h-[100dvh] transition-width duration-200 ease-in-out {$showSidebar
			? 'md:max-w-[calc(100%-var(--sidebar-width))]'
			: ''} max-w-full"
	>
		<nav class="px-4 pt-1 backdrop-blur-xl drag-region">
			<!-- llens(#34): 上部メニューも本文と同じ max-w-5xl 中央寄せに揃える -->
			<div class=" flex items-center gap-1 max-w-5xl mx-auto w-full">
				<div
					class="{$showSidebar ? 'md:hidden' : ''} self-center flex flex-none items-center px-1.5"
				>
					<button
						id="sidebar-toggle-button"
						class="cursor-pointer p-1.5 flex rounded-xl hover:bg-gray-100 dark:hover:bg-gray-850"
						onclick={() => {
							showSidebar.set(!$showSidebar);
						}}
						aria-label="Toggle Sidebar"
					>
						<div class=" m-auto self-center">
							<span class="i-lucide-menu size-5 block"></span>
						</div>
					</button>
				</div>

				<div class="">
					<div
						class="flex gap-1 scrollbar-none overflow-x-auto w-fit text-center text-sm font-medium rounded-full bg-transparent py-1 touch-auto pointer-events-auto"
					>
						<!-- タブは WORKSPACE_TABS(script)で宣言的に定義。見た目はこの 1 ブロックだけで変えられる -->
						{#each visibleWorkspaceTabs as tab (tab.path)}
							<a
								class="min-w-fit rounded-full p-1.5 {$page.url.pathname.includes(
									`/workspace/${tab.path}`
								)
									? ''
									: 'text-content-subtle hover:text-content'}"
								href={`/workspace/${tab.path}`}>{$i18n.t(tab.label)}</a
							>
						{/each}
					</div>
				</div>

				<!-- <div class="flex items-center text-xl font-semibold">{$i18n.t('Workspace')}</div> -->
			</div>
		</nav>

		<div class="pb-1 px-[18px] flex-1 max-h-full overflow-y-auto" id="workspace-container">
			<!-- llens(#34): workspace 全ページの横幅をここで一元管理(ページごとに max-w がばらつくのを防ぐ)。
			     個々のページ/エディタは自前で max-w を持たず、この 5xl 中央寄せを継承する。
			     h-full で高さの連鎖も確定させる(エディタ系ページが h-full + flex-1/min-h-0 で
			     全高を貼れる。一覧ページは h-full を使わないので従来どおり親が縦スクロールを受ける)。 -->
			<div class="max-w-5xl mx-auto w-full px-1.5 h-full">
				{@render children?.()}
			</div>
		</div>
	</div>
{/if}
