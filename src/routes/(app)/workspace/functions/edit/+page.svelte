<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { onMount, getContext } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';

	import { functions, models } from '$lib/stores';
	import {
		createNewFunction,
		deleteFunctionById,
		updateFunctionById,
		getFunctions,
		getFunctionById
	} from '$lib/apis/functions';
	import { getModels } from '$lib/apis';

	import FunctionEditor from '../FunctionEditor.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	// llens(#45): frontmatter/version ゲート(extractFrontmatter/compareVersion)は撤去。
	// llens(#48): Admin を Workspace に統合し /workspace/functions 配下へ移動。
	const i18n = getContext('i18n');

	let func = $state<any>(null);

	const refreshStores = async () => {
		functions.set(await getFunctions());
		models.set(await getModels());
	};

	const saveHandler = async (data: any) => {
		const payload = {
			id: data.id,
			name: data.name,
			description: data.description,
			content: data.content
		};

		// llens(#43 Phase2b): id が変わったら PK は変えられないので、新 id で作成してから旧 id を削除する
		// (差し替え)。作成が失敗(id 衝突/不正)したら旧 id を消さず中断。参照・valves はちぎれてよい。
		if (data.id !== func.id) {
			const created = await createNewFunction(payload).catch((error) => {
				toast.error(`${error}`);
				return null;
			});

			if (!created) return;

			await deleteFunctionById(func.id).catch((error) => {
				toast.error(`${error}`);
			});

			toast.success($i18n.t('Function updated successfully'));
			await refreshStores();
			await goto('/workspace/functions');
			return;
		}

		const res = await updateFunctionById(func.id, payload).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			toast.success($i18n.t('Function updated successfully'));
			await refreshStores();
		}
	};

	onMount(async () => {
		const id = $page.url.searchParams.get('id');

		if (id) {
			func = await getFunctionById(id).catch((error) => {
				toast.error(`${error}`);
				goto('/workspace/functions');
				return null;
			});
		}
	});
</script>

{#if func}
	<FunctionEditor
		edit={true}
		id={func.id}
		name={func.name}
		type={func.type}
		description={func.description}
		content={func.content}
		onSave={saveHandler}
	/>
{:else}
	<div class="flex items-center justify-center h-full">
		<div class=" pb-16">
			<Spinner />
		</div>
	</div>
{/if}
