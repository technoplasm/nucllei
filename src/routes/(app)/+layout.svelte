<script lang="ts">
	import { onMount, tick } from 'svelte';

	import { goto } from '$app/navigation';

	import { getModels } from '$lib/apis';
	import { getSkills } from '$lib/apis/skills';
	import { getTools } from '$lib/apis/tools';
	import { getBanners } from '$lib/apis/configs';
	import { getUserSettings } from '$lib/apis/users';

	import { user, settings, models, skills, tools, banners, showSettings } from '$lib/stores';

	import Sidebar from '$lib/components/layout/Sidebar.svelte';
	import SettingsModal from './SettingsModal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import IdleTimeoutRing from '$lib/components/layout/IdleTimeoutRing.svelte';

	let { children } = $props();

	let loaded = $state(false);

	onMount(async () => {
		if ($user === undefined || $user === null) {
			await goto('/auth');
			return;
		}

		// llens(#59): pending / disabled はアプリに入れず専用ページへ
		if (!['user', 'admin'].includes($user.role)) {
			await goto('/pending');
			return;
		}

		const userSettings = await getUserSettings().catch((error) => {
			console.error(error);
			return null;
		});

		if (userSettings) {
			settings.set(userSettings.ui);
		} else {
			let localStorageSettings = {} as Parameters<(typeof settings)['set']>[0];

			try {
				localStorageSettings = JSON.parse(localStorage.getItem('settings') ?? '{}');
			} catch (e: unknown) {
				console.error('Failed to parse settings from localStorage', e);
			}

			settings.set(localStorageSettings);
		}

		models.set(await getModels());

		banners.set(await getBanners());
		tools.set(await getTools());
		// llens(issue #35): /command ハイライト判定は picker を開く前から必要なため
		// skill 一覧を初期ロードしておく。
		skills.set(await getSkills());

		document.addEventListener('keydown', async function (event) {
			const isCtrlPressed = event.ctrlKey || event.metaKey; // metaKey is for Cmd key on Mac
			// Check if the Shift key is pressed
			const isShiftPressed = event.shiftKey;

			// Check if Ctrl + Shift + O is pressed
			if (isCtrlPressed && isShiftPressed && event.key.toLowerCase() === 'o') {
				event.preventDefault();
				console.log('newChat');
				document.getElementById('sidebar-new-chat-button')?.click();
			}

			// Check if Shift + Esc is pressed
			if (isShiftPressed && event.key === 'Escape') {
				event.preventDefault();
				console.log('focusInput');
				document.getElementById('chat-input')?.focus();
			}

			// Check if Ctrl + Shift + ; is pressed
			if (isCtrlPressed && isShiftPressed && event.key === ';') {
				event.preventDefault();
				console.log('copyLastCodeBlock');
				const button = [...document.getElementsByClassName('copy-code-button')].at(-1);
				if (button instanceof HTMLElement) {
					button.click();
				}
			}

			// Check if Ctrl + Shift + C is pressed
			if (isCtrlPressed && isShiftPressed && event.key.toLowerCase() === 'c') {
				event.preventDefault();
				console.log('copyLastResponse');
				const button = [...document.getElementsByClassName('copy-response-button')].at(-1);
				console.log(button);
				if (button instanceof HTMLElement) {
					button.click();
				}
			}

			// Check if Ctrl + Shift + S is pressed
			if (isCtrlPressed && isShiftPressed && event.key.toLowerCase() === 's') {
				event.preventDefault();
				console.log('toggleSidebar');
				document.getElementById('sidebar-toggle-button')?.click();
			}

			// Check if Ctrl + Shift + Backspace is pressed
			if (
				isCtrlPressed &&
				isShiftPressed &&
				(event.key === 'Backspace' || event.key === 'Delete')
			) {
				event.preventDefault();
				console.log('deleteChat');
				document.getElementById('delete-chat-button')?.click();
			}

			// Check if Ctrl + . is pressed
			if (isCtrlPressed && event.key === '.') {
				event.preventDefault();
				console.log('openSettings');
				showSettings.set(!$showSettings);
			}

			// Check if Ctrl + / is pressed
			if (isCtrlPressed && event.key === '/') {
				event.preventDefault();
				console.log('showShortcuts');
				document.getElementById('show-shortcuts-button')?.click();
			}
		});

		await tick();

		loaded = true;
	});
</script>

<SettingsModal bind:show={$showSettings} />

<!-- アイドルタイムアウトまでの残り時間(右上)。アプリの全画面で同じ位置に出す -->
{#if loaded}
	<IdleTimeoutRing />
{/if}

<div class="app relative">
	<div
		class=" text-content bg-surface h-screen max-h-[100dvh] overflow-auto flex flex-row justify-end"
	>
		<Sidebar />

		{#if loaded}
			{@render children?.()}
		{:else}
			<div class="w-full flex-1 h-full flex items-center justify-center">
				<Spinner />
			</div>
		{/if}
	</div>
</div>
