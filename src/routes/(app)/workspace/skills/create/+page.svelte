<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';
	import { getContext } from 'svelte';

	import { skills } from '$lib/stores';
	import { createNewSkill, getSkills } from '$lib/apis/skills';
	import SkillEditor from '../SkillEditor.svelte';

	const i18n = getContext('i18n');

	// air-gapped: openwebui.com/community 連携(postMessage インポート・sessionStorage clone)は
	// 一切持ち込まない(issue #35)。作成は空フォームからのみ。
	const onSubmit = async (_skill: any) => {
		const skill = await createNewSkill(_skill).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (skill) {
			toast.success($i18n.t('Skill created successfully'));

			await skills.set(await getSkills());
			await goto('/workspace/skills');
		}
	};
</script>

<SkillEditor {onSubmit} />
