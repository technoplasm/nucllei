<script lang="ts">
	let { className = 'size-5' }: { className?: string } = $props();
</script>

<div class="flex justify-center text-center">
	<!-- 旧 SVG スピナーの再現: 薄い全周リング + 上辺 1/4 弧が 0.75s で回転(currentColor 継承) -->
	<div
		class="{className} animate-spin [animation-duration:750ms] rounded-full border-2 border-current/25 border-t-current"
	></div>
</div>
