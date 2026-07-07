<script>
	import { toast } from 'svelte-sonner';
	import { onMount, getContext } from 'svelte';

	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { config, functions, models, settings } from '$lib/stores';
	import {
		createNewFunction,
		deleteFunctionById,
		updateFunctionById,
		getFunctions,
		getFunctionById
	} from '$lib/apis/functions';

	import FunctionEditor from '$lib/components/admin/Functions/FunctionEditor.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { getModels } from '$lib/apis';

	// llens(#45): frontmatter/version ゲート(extractFrontmatter/compareVersion)は撤去。
	const i18n = getContext('i18n');

	let func = null;

	const refreshStores = async () => {
		functions.set(await getFunctions(localStorage.token));
		models.set(
			await getModels(
				localStorage.token,
				$config?.features?.enable_direct_connections && ($settings?.directConnections ?? null)
			)
		);
	};

	const saveHandler = async (data) => {
		const payload = {
			id: data.id,
			name: data.name,
			description: data.description,
			content: data.content
		};

		// llens(#43 Phase2b): id が変わったら PK は変えられないので、新 id で作成してから旧 id を削除する
		// (差し替え)。作成が失敗(id 衝突/不正)したら旧 id を消さず中断。参照・valves はちぎれてよい。
		if (data.id !== func.id) {
			const created = await createNewFunction(localStorage.token, payload).catch((error) => {
				toast.error(`${error}`);
				return null;
			});

			if (!created) return;

			await deleteFunctionById(localStorage.token, func.id).catch((error) => {
				toast.error(`${error}`);
			});

			toast.success($i18n.t('Function updated successfully'));
			await refreshStores();
			await goto('/admin/functions');
			return;
		}

		const res = await updateFunctionById(localStorage.token, func.id, payload).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			toast.success($i18n.t('Function updated successfully'));
			await refreshStores();
		}
	};

	onMount(async () => {
		console.log('mounted');
		const id = $page.url.searchParams.get('id');

		if (id) {
			func = await getFunctionById(localStorage.token, id).catch((error) => {
				toast.error(`${error}`);
				goto('/admin/functions');
				return null;
			});

			console.log(func);
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
		onSave={(value) => {
			saveHandler(value);
		}}
	/>
{:else}
	<div class="flex items-center justify-center h-full">
		<div class=" pb-16">
			<Spinner />
		</div>
	</div>
{/if}
