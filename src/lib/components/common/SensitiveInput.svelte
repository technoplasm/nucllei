<script lang="ts">
	let {
		value = $bindable(''),
		placeholder = '',
		required = true,
		readOnly = false,
		outerClassName = 'flex flex-1 bg-transparent',
		inputClassName = 'w-full text-sm py-0.5 placeholder:text-content-subtle bg-transparent outline-hidden',
		showButtonClassName = 'pl-1.5 bg-transparent'
	}: {
		value?: string;
		placeholder?: string;
		required?: boolean;
		readOnly?: boolean;
		outerClassName?: string;
		inputClassName?: string;
		showButtonClassName?: string;
	} = $props();

	let show = $state(false);
</script>

<div class={outerClassName}>
	<input
		class={`${inputClassName} ${show ? '' : 'password'}`}
		{placeholder}
		bind:value
		required={required && !readOnly}
		disabled={readOnly}
		autocomplete="off"
		type="text"
	/>
	<button
		class={showButtonClassName}
		type="button"
		aria-label={show ? 'Hide value' : 'Show value'}
		onclick={(e) => {
			e.preventDefault();
			show = !show;
		}}
	>
		{#if show}
			<span class="i-lucide-eye-off size-4 block"></span>
		{:else}
			<span class="i-lucide-eye size-4 block"></span>
		{/if}
	</button>
</div>
