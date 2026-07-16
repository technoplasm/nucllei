<script lang="ts">
	import { APP_VERSION } from '$lib/constants';
	import { WEBUI_NAME, config } from '$lib/stores';
	import { onMount, getContext } from 'svelte';

	const i18n = getContext('i18n');

	type LicensedPackage = { name: string; version: string; license: string };

	// 依存クレジット(static/licenses.json — `uv run task licenses` で再生成)
	let licenses = $state<{ frontend: LicensedPackage[]; backend: LicensedPackage[] } | null>(null);

	const groupByLicense = (packages: LicensedPackage[]) => {
		const groups = new Map<string, LicensedPackage[]>();
		for (const pkg of packages) {
			const key = pkg.license || 'Unknown';
			if (!groups.has(key)) {
				groups.set(key, []);
			}
			groups.get(key)!.push(pkg);
		}
		return [...groups.entries()].sort((a, b) => b[1].length - a[1].length);
	};

	onMount(async () => {
		licenses = await fetch('/licenses.json')
			.then((res) => (res.ok ? res.json() : null))
			.catch(() => null);
	});
</script>

<div class="flex flex-col h-full justify-between space-y-3 text-sm mb-6">
	<div class=" space-y-3 flex-1 min-h-0 overflow-y-auto">
		<!-- 上段: 配備層(WEBUI_NAME でブランドされたハーネス/エージェント設計。
		     LLENS 等。権利表記は WEBUI_COPYRIGHT env — 無ければ段ごと出さない) -->
		{#if $WEBUI_NAME !== 'Nucllei' || $config?.copyright}
			<div>
				<div class="flex items-baseline gap-2">
					<span class="text-base font-medium">{$WEBUI_NAME}</span>
					{#if $config?.webui_version}
						<span class="text-xs text-content-muted">{$config.webui_version}</span>
					{/if}
				</div>

				{#if $config?.copyright}
					<div class="mt-1 text-xs text-content-subtle leading-relaxed">
						{$config.copyright}
					</div>
				{/if}
			</div>
		{/if}

		<!-- 中段: 本体(Nucllei) -->
		<div>
			<div class="flex items-baseline gap-2">
				<span class="text-base font-medium">Nucllei</span>
				<span class="text-xs text-content-muted">{APP_VERSION}</span>
			</div>

			<div class="mt-1 text-xs text-content-subtle leading-relaxed">
				Nucllei is a platform for hosting LLM AI agent harnesses, forked from <span
					class="whitespace-nowrap">Open WebUI</span
				>
				v0.6.5. Upstream code is available under the BSD-3-Clause license, and modifications and additions
				are Copyright © Technoplasm, Inc.
				<span class="whitespace-nowrap">All rights reserved.</span>
			</div>
		</div>

		<hr class=" border-line" />

		<!-- 下段: 関連システムのライセンス -->
		<div>
			<div class="text-sm font-medium">{$i18n.t('Third-Party Licenses')}</div>

			<div class="mt-2">
				<div class="flex items-baseline gap-2 text-content-muted">
					<span class="text-sm">Open WebUI v0.6.5</span>
					<span class="text-xs">(fork base, BSD-3-Clause)</span>
				</div>

				<pre class="mt-1 text-xs text-content-subtle whitespace-pre-wrap">Copyright (c) 2023 <a
						href="https://openwebui.com"
						target="_blank"
						class="underline">Open WebUI (Timothy Jaeryang Baek)</a
					>
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
</pre>
			</div>

			{#if licenses}
				<hr class=" border-line my-3" />

				<div>
					<div class="text-sm font-medium mb-1">{$i18n.t('Third-Party Packages')}</div>

					{#each [{ title: 'Frontend', packages: licenses.frontend }, { title: 'Backend', packages: licenses.backend }] as side (side.title)}
						<div class="mt-2">
							<div class="text-xs font-medium text-content-muted">
								{side.title} ({side.packages.length})
							</div>

							{#each groupByLicense(side.packages) as [license, packages] (license)}
								<div class="mt-1.5">
									<div class="text-xs text-content-muted">{license} ({packages.length})</div>
									<div class="text-xs text-content-subtle leading-relaxed break-words">
										{packages.map((pkg) => `${pkg.name} ${pkg.version}`).join(', ')}
									</div>
								</div>
							{/each}
						</div>
					{/each}
				</div>
			{/if}
		</div>
	</div>
</div>
