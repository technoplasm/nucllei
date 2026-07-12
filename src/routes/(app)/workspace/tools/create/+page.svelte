<script>
	import { goto } from '$app/navigation';
	import { createNewTool, getTools } from '$lib/apis/tools';
	import ToolkitEditor from '$lib/components/workspace/Tools/ToolkitEditor.svelte';
	import { tools } from '$lib/stores';
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	// llens(#44): frontmatter/version ゲートと openwebui.com community 連携(share 受け側の
	// postMessage)・clone(sessionStorage 経由)は撤去。素の作成フローのみ。
	const i18n = getContext('i18n');

	let mounted = false;

	const saveHandler = async (data) => {
		const res = await createNewTool({
			id: data.id,
			name: data.name,
			description: data.description,
			content: data.content,
			access_control: data.access_control
		}).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			toast.success($i18n.t('Tool created successfully'));
			tools.set(await getTools());

			await goto('/workspace/tools');
		}
	};

	onMount(() => {
		mounted = true;
	});
</script>

{#if mounted}
	<ToolkitEditor
		onSave={(value) => {
			saveHandler(value);
		}}
	/>
{/if}
