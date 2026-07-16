<script lang="ts">
	import { createEventDispatcher, tick } from 'svelte';
	import { Switch } from 'bits-ui';
	export let state = true;

	const dispatch = createEventDispatcher();

	// Svelte 5(互換モード)では初期化時の $: dispatch が親のリスナーに届いてしまい、
	// マウントしただけで on:change 側の副作用(API 呼び出し等)が発火する。
	// 初期値を除き、state が実際に変化したときだけ change を発火する。
	let lastState = state;
	$: if (state !== lastState) {
		lastState = state;
		dispatch('change', state);
	}
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
