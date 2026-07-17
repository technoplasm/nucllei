<script lang="ts">
	import RecursiveFolder from './RecursiveFolder.svelte';
	import type { SidebarFolder } from './RecursiveFolder.svelte';

	let {
		folders = {},
		onImport = undefined,
		onUpdate = undefined,
		onChange = undefined
	}: {
		folders?: Record<string, SidebarFolder>;
		onImport?: (payload: { folderId: string; items: any[] }) => void;
		onUpdate?: () => void;
		onChange?: () => void;
	} = $props();

	// Get the list of folders that have no parent, sorted by name alphabetically
	const folderList = $derived(
		Object.keys(folders)
			.filter((key) => folders[key].parent_id === null)
			.sort((a, b) =>
				folders[a].name.localeCompare(folders[b].name, undefined, {
					numeric: true,
					sensitivity: 'base'
				})
			)
	);
</script>

{#each folderList as folderId (folderId)}
	<RecursiveFolder className="" {folders} {folderId} {onImport} {onUpdate} {onChange} />
{/each}
