<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';
	import { onMount, getContext } from 'svelte';
	import { page } from '$app/stores';

	import { models } from '$lib/stores';
	import { createNewModel, deleteModelById, getModelById, updateModelById } from '$lib/apis/models';
	import { getModels } from '$lib/apis';

	import ModelEditor from '../ModelEditor.svelte';

	const i18n = getContext('i18n');

	let model = $state<any>(null);

	const refreshModels = async () => {
		await models.set(await getModels());
	};

	const onSubmit = async (modelInfo: any) => {
		// llens(#43 Phase2b): id が変わったら PK は変えられないので、新 id で作成してから旧 id を削除する
		// (差し替え)。作成が失敗(id 衝突/不正)したら旧 id を消さず中断。既存チャットの参照はちぎれてよい。
		if (modelInfo.id !== model.id) {
			const created = await createNewModel(modelInfo).catch((error) => {
				toast.error(`${error}`);
				return null;
			});

			if (!created) return;

			await deleteModelById(model.id).catch((error) => {
				toast.error(`${error}`);
			});

			await refreshModels();
			toast.success($i18n.t('Model updated successfully'));
			await goto('/workspace/models');
			return;
		}

		const res = await updateModelById(modelInfo.id, modelInfo);

		if (res) {
			await refreshModels();
			toast.success($i18n.t('Model updated successfully'));
			await goto('/workspace/models');
		}
	};

	onMount(async () => {
		const id = $page.url.searchParams.get('id');
		if (id) {
			model = await getModelById(id).catch(() => null);

			if (!model) {
				goto('/workspace/models');
			}
		} else {
			goto('/workspace/models');
		}
	});
</script>

{#if model}
	<ModelEditor edit {model} {onSubmit} />
{/if}
