<script>
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import {
		createNewTool,
		deleteToolById,
		getToolById,
		getTools,
		updateToolById
	} from '$lib/apis/tools';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import ToolkitEditor from '$lib/components/workspace/Tools/ToolkitEditor.svelte';
	import { tools } from '$lib/stores';
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	// llens(#44): frontmatter 由来の version ゲートは撤去。builtin は直リンクでも編集不可。
	const i18n = getContext('i18n');

	let tool = null;

	const saveHandler = async (data) => {
		const payload = {
			id: data.id,
			name: data.name,
			description: data.description,
			content: data.content,
			access_control: data.access_control
		};

		// llens(#43 Phase2b): id が変わったら PK は変えられないので、新 id で作成してから旧 id を削除する
		// (差し替え)。作成が失敗(id 衝突/不正)したら旧 id を消さず中断。参照・valves はちぎれてよい。
		if (data.id !== tool.id) {
			const created = await createNewTool(localStorage.token, payload).catch((error) => {
				toast.error(`${error}`);
				return null;
			});

			if (!created) return;

			await deleteToolById(localStorage.token, tool.id).catch((error) => {
				toast.error(`${error}`);
			});

			toast.success($i18n.t('Tool updated successfully'));
			tools.set(await getTools(localStorage.token));
			await goto('/workspace/tools');
			return;
		}

		const res = await updateToolById(localStorage.token, tool.id, payload).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			toast.success($i18n.t('Tool updated successfully'));
			tools.set(await getTools(localStorage.token));

			// await goto('/workspace/tools');
		}
	};

	onMount(async () => {
		const id = $page.url.searchParams.get('id');

		if (id) {
			tool = await getToolById(localStorage.token, id).catch((error) => {
				toast.error(`${error}`);
				goto('/workspace/tools');
				return null;
			});

			// llens(#44): builtin は編集不可。直リンクで来ても編集画面に入れない。
			if (tool?.meta?.builtin) {
				toast.error($i18n.t('Builtin tools cannot be edited.'));
				tool = null;
				await goto('/workspace/tools');
			}
		}
	});
</script>

{#if tool}
	<ToolkitEditor
		edit={true}
		id={tool.id}
		name={tool.name}
		description={tool.description}
		content={tool.content}
		accessControl={tool.access_control}
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
