<script lang="ts">
	import { Switch } from 'bits-ui';

	let {
		state = $bindable(true),
		onChange = undefined
	}: {
		state?: boolean;
		onChange?: (state: boolean) => void;
	} = $props();

	// 初期値では発火させず、state が実際に変化したときだけ onChange を呼ぶ
	// (初期化時の反応が親側の副作用(API 保存等)を発火させた前歴があるため)。
	let lastState = state;
	$effect(() => {
		if (state !== lastState) {
			lastState = state;
			onChange?.(state);
		}
	});
</script>

<!-- llens(#34): 一回り小型化(h-5 w-9 → h-4 w-7)。unchecked の dark:bg-transparent は
     「透明面」問題(面が無く押せるものに見えない)なので track を bg-line の実面に -->
<Switch.Root
	bind:checked={state}
	class="flex h-4 min-h-4 w-7 shrink-0 cursor-pointer items-center rounded-full px-[2px] mx-[1px] {state
		? ' bg-emerald-600'
		: 'bg-line'}"
>
	<Switch.Thumb
		class="pointer-events-none block size-3 shrink-0 rounded-full bg-white transition-transform data-[state=checked]:translate-x-3 data-[state=unchecked]:translate-x-0 data-[state=unchecked]:shadow-mini "
	/>
</Switch.Root>
