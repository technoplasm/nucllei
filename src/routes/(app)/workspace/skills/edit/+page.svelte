<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';
	import { skills } from '$lib/stores';
	import { onMount, getContext } from 'svelte';

	const i18n = getContext('i18n');

	import { getSkillByCommand, getSkills, updateSkillByCommand } from '$lib/apis/skills';
	import { page } from '$app/stores';

	import SkillEditor from '$lib/components/workspace/Skills/SkillEditor.svelte';

	let skill = null;
	const onSubmit = async (_skill) => {
		const skill = await updateSkillByCommand(localStorage.token, _skill).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (skill) {
			toast.success($i18n.t('Skill updated successfully'));
			await skills.set(await getSkills(localStorage.token));
			await goto('/workspace/skills');
		}
	};

	onMount(async () => {
		const command = $page.url.searchParams.get('command');
		if (command) {
			const _skill = await getSkillByCommand(localStorage.token, command.replace(/\//g, '')).catch(
				(error) => {
					toast.error(`${error}`);
					return null;
				}
			);

			if (_skill) {
				skill = {
					command: _skill.command,
					title: _skill.title,
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
