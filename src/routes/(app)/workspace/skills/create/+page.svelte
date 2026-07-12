<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';
	import { skills } from '$lib/stores';
	import { onMount, getContext } from 'svelte';

	const i18n = getContext('i18n');

	import { createNewSkill, getSkills } from '$lib/apis/skills';
	import SkillEditor from '$lib/components/workspace/Skills/SkillEditor.svelte';

	// air-gapped: openwebui.com/community 連携(postMessage インポート・sessionStorage clone)は
	// 一切持ち込まない(issue #35)。作成は空フォームからのみ。
	let skill = null;
	const onSubmit = async (_skill) => {
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

{#key skill}
	<SkillEditor {skill} {onSubmit} />
{/key}
