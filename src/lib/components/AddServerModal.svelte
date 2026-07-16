<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { getContext, onMount } from 'svelte';
	const i18n = getContext('i18n');

	import Modal from '$lib/components/common/Modal.svelte';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import { getToolServerData } from '$lib/apis';
	import { verifyToolServerConnection } from '$lib/apis/configs';
	import AccessControl from './workspace/common/AccessControl.svelte';

	let {
		show = $bindable(false),
		edit = false,
		direct = false,
		connection = null,
		onSubmit = () => {},
		onDelete = () => {}
	}: {
		show?: boolean;
		edit?: boolean;
		direct?: boolean;
		connection?: any;
		onSubmit?: (connection: any) => void | Promise<void>;
		onDelete?: () => void;
	} = $props();

	let url = $state('');
	let path = $state('openapi.json');

	let auth_type = $state('bearer');
	let key = $state('');

	let accessControl = $state<any>({});

	let enable = $state(true);

	let loading = $state(false);

	const verifyHandler = async () => {
		if (url === '') {
			toast.error($i18n.t('Please enter a valid URL'));
			return;
		}

		if (path === '') {
			toast.error($i18n.t('Please enter a valid path'));
			return;
		}

		if (direct) {
			// llens(#70 B1): session 型はサーバ側実行時に backend が資格情報を転送する。
			// フロントの疎通テストは cookie(httpOnly)を読めないため無認証で叩く。
			const res = await getToolServerData(
				auth_type === 'bearer' ? key : '',
				`${url}/${path}`
			).catch(() => {
				toast.error($i18n.t('Connection failed'));
			});

			if (res) {
				toast.success($i18n.t('Connection successful'));
			}
		} else {
			const res = await verifyToolServerConnection({
				url,
				path,
				auth_type,
				key,
				config: {
					enable: enable,
					access_control: accessControl
				}
			}).catch(() => {
				toast.error($i18n.t('Connection failed'));
			});

			if (res) {
				toast.success($i18n.t('Connection successful'));
			}
		}
	};

	const submitHandler = async () => {
		loading = true;

		// remove trailing slash from url
		url = url.replace(/\/$/, '');

		const connection = {
			url,
			path,
			auth_type,
			key,
			config: {
				enable: enable,
				access_control: accessControl
			}
		};

		await onSubmit(connection);

		loading = false;
		show = false;

		url = '';
		path = 'openapi.json';
		key = '';
		auth_type = 'bearer';

		enable = true;
		accessControl = null;
	};

	const init = () => {
		if (connection) {
			url = connection.url;
			path = connection?.path ?? 'openapi.json';

			auth_type = connection?.auth_type ?? 'bearer';
			key = connection?.key ?? '';

			enable = connection.config?.enable ?? true;
			accessControl = connection.config?.access_control ?? null;
		}
	};

	$effect(() => {
		if (show) {
			init();
		}
	});

	onMount(() => {
		init();
	});
</script>

<Modal size="sm" bind:show>
	<div>
		<div class=" flex justify-between text-content px-5 pt-4 pb-2">
			<div class=" text-lg font-medium self-center font-primary">
				{#if edit}
					{$i18n.t('Edit Connection')}
				{:else}
					{$i18n.t('Add Connection')}
				{/if}
			</div>
			<button
				class="self-center"
				type="button"
				aria-label={$i18n.t('Close')}
				onclick={() => {
					show = false;
				}}
			>
				<span class="i-lucide-x size-5 block"></span>
			</button>
		</div>

		<div class="flex flex-col w-full px-5 pb-4 text-content">
			<form
				class="flex flex-col w-full"
				onsubmit={(e) => {
					e.preventDefault();
					submitHandler();
				}}
			>
				<div class="flex gap-2 items-end">
					<div class="flex flex-col flex-1 min-w-0">
						<div class=" mb-1 text-xs text-content-muted">{$i18n.t('URL')}</div>

						<input
							class="w-full text-sm px-2 py-1 bg-surface-muted rounded-xs outline-hidden"
							type="text"
							bind:value={url}
							placeholder={$i18n.t('API Base URL')}
							autocomplete="off"
							required
						/>
					</div>

					<Tooltip content={$i18n.t('Verify Connection')} className="shrink-0">
						<button
							class="p-1.5 bg-transparent hover:bg-surface-hover rounded-lg"
							aria-label={$i18n.t('Verify Connection')}
							onclick={() => {
								verifyHandler();
							}}
							type="button"
						>
							<span class="i-lucide-refresh-cw size-4 block"></span>
						</button>
					</Tooltip>

					<Tooltip
						content={enable ? $i18n.t('Enabled') : $i18n.t('Disabled')}
						className="shrink-0 flex items-center h-7"
					>
						<Switch bind:state={enable} />
					</Tooltip>
				</div>

				<div class="flex flex-col w-full mt-2">
					<div class=" mb-1 text-xs text-content-muted">{$i18n.t('openapi.json Path')}</div>
					<div class="flex items-center gap-1">
						<div class="text-sm text-content-muted">/</div>
						<input
							class="w-full text-sm px-2 py-1 bg-surface-muted rounded-xs outline-hidden"
							type="text"
							bind:value={path}
							placeholder={$i18n.t('openapi.json Path')}
							autocomplete="off"
							required
						/>
					</div>

					<div class="text-xs text-content-muted mt-1">
						{$i18n.t(`WebUI will make requests to "{{url}}"`, {
							url: `${url}/${path}`
						})}
					</div>
				</div>

				<div class="flex flex-col w-full mt-2">
					<div class=" mb-1 text-xs text-content-muted">{$i18n.t('Auth')}</div>

					<div class="flex gap-2">
						<div class="shrink-0 self-start">
							<select
								class="text-sm px-2 py-1 pr-6 bg-surface-muted rounded-xs outline-hidden"
								bind:value={auth_type}
							>
								<option value="bearer">Bearer</option>
								<option value="session">Session</option>
							</select>
						</div>

						<div class="flex flex-1 min-w-0 items-center">
							{#if auth_type === 'bearer'}
								<SensitiveInput
									outerClassName="flex w-full px-2 bg-surface-muted rounded-xs"
									inputClassName="w-full text-sm py-1 placeholder:text-content-subtle bg-transparent outline-hidden"
									bind:value={key}
									placeholder={$i18n.t('API Key')}
									required={false}
								/>
							{:else if auth_type === 'session'}
								<div class="text-xs text-content-muted self-center">
									{$i18n.t('Forwards system user session credentials to authenticate')}
								</div>
							{/if}
						</div>
					</div>
				</div>

				{#if !direct}
					<hr class=" border-line my-2.5 w-full" />

					<div class="my-2">
						<div class="px-3 py-2 bg-surface-muted rounded-lg">
							<AccessControl bind:accessControl />
						</div>
					</div>
				{/if}

				<div class="flex justify-end pt-3 text-sm font-medium gap-1.5">
					{#if edit}
						<button
							class="px-3.5 py-1.5 text-sm font-medium bg-secondary hover:bg-secondary-hover text-secondary-content rounded-full"
							type="button"
							onclick={() => {
								onDelete();
								show = false;
							}}
						>
							{$i18n.t('Delete')}
						</button>
					{/if}

					<button
						class="px-3.5 py-1.5 text-sm font-medium bg-primary hover:bg-primary-hover text-primary-content rounded-full flex flex-row space-x-1 items-center {loading
							? ' cursor-not-allowed'
							: ''}"
						type="submit"
						disabled={loading}
					>
						{$i18n.t('Save')}

						{#if loading}
							<div class="ml-2 self-center">
								<Spinner className="size-4" />
							</div>
						{/if}
					</button>
				</div>
			</form>
		</div>
	</div>
</Modal>
