<script>
	import { toast } from 'svelte-sonner';
	import { onMount, getContext } from 'svelte';
	import { goto } from '$app/navigation';

	import { config, functions, models, settings } from '$lib/stores';
	import { createNewFunction, getFunctions } from '$lib/apis/functions';
	import FunctionEditor from '$lib/components/admin/Functions/FunctionEditor.svelte';
	import { getModels } from '$lib/apis';

	// llens(#45): frontmatter/version ゲートと openwebui.com community 連携(share 受け側の
	// postMessage)・clone(sessionStorage 経由)は撤去。素の作成フローのみ。
	// llens(#48): Admin を Workspace に統合し /workspace/functions 配下へ移動。
	const i18n = getContext('i18n');

	let mounted = false;

	const saveHandler = async (data) => {
		const res = await createNewFunction(localStorage.token, {
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
			functions.set(await getFunctions(localStorage.token));
			models.set(
				await getModels(
					localStorage.token,
					$config?.features?.enable_direct_connections && ($settings?.directConnections ?? null)
				)
			);

			await goto('/workspace/functions');
		}
	};

	onMount(() => {
		mounted = true;
	});
</script>

{#if mounted}
	<FunctionEditor
		onSave={(value) => {
			saveHandler(value);
		}}
	/>
{/if}
