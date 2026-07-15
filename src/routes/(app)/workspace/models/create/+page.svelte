<script lang="ts">
	// llens(#70 A2): openwebui.com community からの postMessage 受け入れ(外部 origin の
	// モデル取り込み導線)は #10 のストア導線撤去と同じ線で削除。作成の seed は
	// 一覧の Clone(sessionStorage.model)のみ。
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';
	import { onMount, getContext } from 'svelte';

	import { models } from '$lib/stores';
	import { createNewModel } from '$lib/apis/models';
	import { getModels } from '$lib/apis';

	import ModelEditor from '../ModelEditor.svelte';

	const i18n = getContext('i18n');

	let model = $state<any>(null);
	let loaded = $state(false);

	const onSubmit = async (modelInfo: any) => {
		if ($models.find((m) => m.id === modelInfo.id)) {
			toast.error(
				$i18n.t(`Error: A model with the ID '{{modelId}}' already exists.`, {
					modelId: modelInfo.id
				})
			);
			return;
		}

		const res = await createNewModel({
			...modelInfo,
			meta: {
				...modelInfo.meta,
				profile_image_url: modelInfo.meta.profile_image_url ?? null,
				suggestion_prompts: modelInfo.meta.suggestion_prompts
					? modelInfo.meta.suggestion_prompts.filter((prompt: any) => prompt.content !== '')
					: null
			},
			params: { ...modelInfo.params }
		}).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			await models.set(await getModels());
			toast.success($i18n.t('Model created successfully!'));
			await goto('/workspace/models');
		}
	};

	onMount(() => {
		if (sessionStorage.model) {
			model = JSON.parse(sessionStorage.model);
			sessionStorage.removeItem('model');
		}
		loaded = true;
	});
</script>

{#if loaded}
	<ModelEditor {model} {onSubmit} />
{/if}
