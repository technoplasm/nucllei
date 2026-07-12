<script lang="ts">
	import { showSidebar } from '$lib/stores';
	import MenuLines from '$lib/components/icons/MenuLines.svelte';
</script>

<!-- llens(#69): ファイル管理ページの骨格(workspace +layout と同型 — タブは無し) -->
<div
	class=" relative flex flex-col w-full h-screen max-h-[100dvh] transition-width duration-200 ease-in-out {$showSidebar
		? 'md:max-w-[calc(100%-260px)]'
		: ''} max-w-full"
>
	<nav class="   px-2.5 pt-1 backdrop-blur-xl drag-region">
		<div class=" flex items-center gap-1 py-1">
			<div class="{$showSidebar ? 'md:hidden' : ''} self-center flex flex-none items-center">
				<button
					id="sidebar-toggle-button"
					class="cursor-pointer p-1.5 flex rounded-xl hover:bg-gray-100 dark:hover:bg-gray-850 transition"
					on:click={() => {
						showSidebar.set(!$showSidebar);
					}}
					aria-label="Toggle Sidebar"
				>
					<div class=" m-auto self-center">
						<MenuLines />
					</div>
				</button>
			</div>
		</div>
	</nav>

	<div class="  pb-1 px-[18px] flex-1 max-h-full overflow-y-auto" id="files-container">
		<slot />
	</div>
</div>
