<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';
	import { skills } from '$lib/stores';
	import { onMount, getContext } from 'svelte';

	const i18n = getContext('i18n');

	import {
		createNewSkill,
		deleteSkillById,
		getSkillById,
		getSkills,
		updateSkillById
	} from '$lib/apis/skills';
	import { page } from '$app/stores';

	import SkillEditor from '$lib/components/workspace/Skills/SkillEditor.svelte';

	let skill = null;
	const onSubmit = async (_skill) => {
		const oldId = skill.id;

		// llens(#43 Phase2b): id が変わったら PK は変えられないので、新 id で作成してから旧 id を削除する
		// (差し替え)。作成が失敗(id 衝突/不正)したら旧 id を消さず中断。参照・valves はちぎれてよい。
		if (_skill.id !== oldId) {
			const created = await createNewSkill(_skill).catch((error) => {
				toast.error(`${error}`);
				return null;
			});

			if (!created) return;

			await deleteSkillById(oldId).catch((error) => {
				toast.error(`${error}`);
			});

			toast.success($i18n.t('Skill updated successfully'));
			await skills.set(await getSkills());
			await goto('/workspace/skills');
			return;
		}

		const updated = await updateSkillById(_skill).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (updated) {
			toast.success($i18n.t('Skill updated successfully'));
			await skills.set(await getSkills());
			await goto('/workspace/skills');
		}
	};

	onMount(async () => {
		const id = $page.url.searchParams.get('id');
		if (id) {
			const _skill = await getSkillById(id.replace(/\//g, '')).catch((error) => {
				toast.error(`${error}`);
				return null;
			});

			if (_skill) {
				skill = {
					id: _skill.id,
					// llens(#66): owner 判定(編集は自分の skill だけ)に使う
					user_id: _skill.user_id,
					name: _skill.name,
					description: _skill.description ?? '',
					content: _skill.content,
					tool_ids: _skill?.tool_ids ?? [],
					access_control: _skill?.access_control ?? null
				};
			} else {
				goto('/workspace/skills');
			}
		} else {
			goto('/workspace/skills');
		}
	});
</script>

{#if skill}
	<SkillEditor {skill} {onSubmit} edit />
{/if}
