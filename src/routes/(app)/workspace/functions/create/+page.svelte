<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { onMount, getContext } from 'svelte';
	import { goto } from '$app/navigation';

	import { functions, models } from '$lib/stores';
	import { createNewFunction, getFunctions } from '$lib/apis/functions';
	import { getModels } from '$lib/apis';
	import FunctionEditor from '../FunctionEditor.svelte';

	const i18n = getContext('i18n');

	// llens(#45): frontmatter/version ゲートと openwebui.com community 連携(share 受け側の
	// postMessage)・clone(sessionStorage 経由)は撤去。素の作成フローのみ。
	// llens(#48): Admin を Workspace に統合し /workspace/functions 配下へ移動。
	let mounted = $state(false);

	const saveHandler = async (data: any) => {
		const res = await createNewFunction({
			id: data.id,
			name: data.name,
			description: data.description,
			content: data.content
		}).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			toast.success($i18n.t('Function created successfully'));
			functions.set(await getFunctions());
			models.set(await getModels());

			await goto('/workspace/functions');
		}
	};

	onMount(() => {
		mounted = true;
	});
</script>

{#if mounted}
	<FunctionEditor onSave={saveHandler} />
{/if}
