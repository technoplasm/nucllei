<script>
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';

	import { onMount, getContext } from 'svelte';
	const i18n = getContext('i18n');

	import { page } from '$app/stores';
	import { config, models, settings } from '$lib/stores';

	import { createNewModel, deleteModelById, getModelById, updateModelById } from '$lib/apis/models';

	import { getModels } from '$lib/apis';
	import ModelEditor from '$lib/components/workspace/Models/ModelEditor.svelte';

	let model = null;

	const refreshModels = async () => {
		await models.set(await getModels(localStorage.token));
	};

	onMount(async () => {
		const _id = $page.url.searchParams.get('id');
		if (_id) {
			model = await getModelById(localStorage.token, _id).catch((e) => {
				return null;
			});

			if (!model) {
				goto('/workspace/models');
			}
		} else {
			goto('/workspace/models');
		}
	});

	const onSubmit = async (modelInfo) => {
		// llens(#43 Phase2b): id が変わったら PK は変えられないので、新 id で作成してから旧 id を削除する
		// (差し替え)。作成が失敗(id 衝突/不正)したら旧 id を消さず中断。既存チャットの参照はちぎれてよい。
		if (modelInfo.id !== model.id) {
			const created = await createNewModel(localStorage.token, modelInfo).catch((error) => {
				toast.error(`${error}`);
				return null;
			});

			if (!created) return;

			await deleteModelById(localStorage.token, model.id).catch((error) => {
				toast.error(`${error}`);
			});

			await refreshModels();
			toast.success($i18n.t('Model updated successfully'));
			await goto('/workspace/models');
			return;
		}

		const res = await updateModelById(localStorage.token, modelInfo.id, modelInfo);

		if (res) {
			await refreshModels();
			toast.success($i18n.t('Model updated successfully'));
			await goto('/workspace/models');
		}
	};
</script>

{#if model}
	<ModelEditor edit={true} {model} {onSubmit} />
{/if}
