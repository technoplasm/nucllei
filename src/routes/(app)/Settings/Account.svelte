<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { onMount, getContext } from 'svelte';

	import { user, config, settings } from '$lib/stores';
	import { updateUserProfile, createAPIKey, getAPIKey, getSessionUser } from '$lib/apis/auths';

	import UpdatePassword from './Account/UpdatePassword.svelte';
	import { generateInitialsImage, canvasPixelTest } from '$lib/utils';
	import { copyToClipboard } from '$lib/utils';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';

	const i18n = getContext('i18n');

	let {
		saveHandler,
		saveSettings
	}: {
		saveHandler: () => void;
		saveSettings: (updated: any) => void | Promise<void>;
	} = $props();

	let profileImageUrl = $state('');
	let name = $state('');

	let webhookUrl = $state('');

	let APIKey = $state('');
	let APIKeyCopied = $state(false);
	let profileImageInputElement = $state<HTMLInputElement | null>(null);

	const submitHandler = async () => {
		if (name !== $user?.name) {
			if (profileImageUrl === generateInitialsImage($user?.name) || profileImageUrl === '') {
				profileImageUrl = generateInitialsImage(name);
			}
		}

		if (webhookUrl !== $settings?.notifications?.webhook_url) {
			saveSettings({
				notifications: {
					...$settings.notifications,
					webhook_url: webhookUrl
				}
			});
		}

		const updatedUser = await updateUserProfile(name, profileImageUrl).catch((error) => {
			toast.error(`${error}`);
		});

		if (updatedUser) {
			// Get Session User Info
			const sessionUser = await getSessionUser().catch((error) => {
				toast.error(`${error}`);
				return null;
			});

			await user.set(sessionUser);
			return true;
		}
		return false;
	};

	const createAPIKeyHandler = async () => {
		APIKey = await createAPIKey();
		if (APIKey) {
			toast.success($i18n.t('API Key created.'));
		} else {
			toast.error($i18n.t('Failed to create API Key.'));
		}
	};

	onMount(async () => {
		name = $user?.name ?? '';
		profileImageUrl = $user?.profile_image_url ?? '';
		webhookUrl = $settings?.notifications?.webhook_url ?? '';

		APIKey = await getAPIKey().catch(() => '');
	});
</script>

<div class="flex flex-col h-full justify-between text-sm">
	<div class=" space-y-3 flex-1 min-h-0 overflow-y-auto">
		<input
			id="profile-image-input"
			bind:this={profileImageInputElement}
			type="file"
			hidden
			accept="image/*"
			onchange={() => {
				const files = profileImageInputElement?.files ?? [];
				let reader = new FileReader();
				reader.onload = (event) => {
					let originalImageUrl = `${event.target?.result}`;

					const img = new Image();
					img.src = originalImageUrl;

					img.onload = function () {
						const canvas = document.createElement('canvas');
						const ctx = canvas.getContext('2d');
						if (!ctx) {
							return;
						}

						// Calculate the aspect ratio of the image
						const aspectRatio = img.width / img.height;

						// Calculate the new width and height to fit within 250x250
						let newWidth, newHeight;
						if (aspectRatio > 1) {
							newWidth = 250 * aspectRatio;
							newHeight = 250;
						} else {
							newWidth = 250;
							newHeight = 250 / aspectRatio;
						}

						// Set the canvas size
						canvas.width = 250;
						canvas.height = 250;

						// Calculate the position to center the image
						const offsetX = (250 - newWidth) / 2;
						const offsetY = (250 - newHeight) / 2;

						// Draw the image on the canvas
						ctx.drawImage(img, offsetX, offsetY, newWidth, newHeight);

						// Get the base64 representation of the compressed image
						const compressedSrc = canvas.toDataURL('image/jpeg');

						// Display the compressed image
						profileImageUrl = compressedSrc;

						if (profileImageInputElement) {
							profileImageInputElement.value = '';
						}
					};
				};

				if (
					files.length > 0 &&
					['image/gif', 'image/webp', 'image/jpeg', 'image/png'].includes(files[0]['type'])
				) {
					reader.readAsDataURL(files[0]);
				}
			}}
		/>

		<div class="space-y-1">
			<div class="flex space-x-5">
				<div class="flex flex-col">
					<div class="self-center mt-2">
						<button
							class="relative rounded-full"
							type="button"
							aria-label={$i18n.t('Profile Image')}
							onclick={() => {
								profileImageInputElement?.click();
							}}
						>
							<img
								src={profileImageUrl !== '' ? profileImageUrl : generateInitialsImage(name)}
								alt="profile"
								class=" rounded-full size-16 object-cover"
							/>

							<div
								class="absolute flex justify-center rounded-full bottom-0 left-0 right-0 top-0 h-full w-full overflow-hidden bg-gray-700 bg-fixed opacity-0 hover:opacity-50"
							>
								<div class="my-auto text-white">
									<span class="i-lucide-pencil size-5 block"></span>
								</div>
							</div>
						</button>
					</div>
				</div>

				<div class="flex-1 flex flex-col self-center gap-0.5">
					<div class=" mb-0.5 text-sm font-medium">{$i18n.t('Profile Image')}</div>

					<div>
						<button
							class=" text-xs text-center rounded-full px-4 py-0.5 bg-secondary hover:bg-secondary-hover text-secondary-content"
							type="button"
							onclick={async () => {
								if (canvasPixelTest()) {
									profileImageUrl = generateInitialsImage(name);
								} else {
									toast.info(
										$i18n.t(
											'Fingerprint spoofing detected: Unable to use initials as avatar. Defaulting to default profile image.'
										),
										{
											duration: 1000 * 10
										}
									);
								}
							}}>{$i18n.t('Use Initials')}</button
						>

						<button
							class=" text-xs text-center text-content-muted hover:text-content rounded-lg px-2 py-1"
							type="button"
							onclick={() => {
								profileImageUrl = '/user.png';
							}}>{$i18n.t('Remove')}</button
						>
					</div>
				</div>
			</div>

			<div class="pt-0.5">
				<label class="flex flex-col w-full">
					<div class=" mb-1 text-xs font-medium">{$i18n.t('Name')}</div>

					<input
						class="w-full text-sm px-2 py-1 bg-surface-muted rounded-xs outline-hidden"
						type="text"
						bind:value={name}
						required
						placeholder={$i18n.t('Enter your name')}
					/>
				</label>
			</div>

			{#if $config?.features?.enable_user_webhooks}
				<div class="pt-2">
					<label class="flex flex-col w-full">
						<div class=" mb-1 text-xs font-medium">{$i18n.t('Notification Webhook')}</div>

						<input
							class="w-full text-sm px-2 py-1 bg-surface-muted rounded-xs outline-hidden"
							type="url"
							placeholder={$i18n.t('Enter your webhook URL')}
							bind:value={webhookUrl}
							required
						/>
					</label>
				</div>
			{/if}
		</div>

		<div class="py-0.5">
			<UpdatePassword />
		</div>

		{#if $config?.features?.enable_api_key ?? true}
			<hr class="border-line my-2" />

			<div class="justify-between w-full">
				<div class="flex justify-between w-full">
					<div class="self-center text-sm font-medium">{$i18n.t('API keys')}</div>
				</div>
				<!-- llens(#70 B1): JWT Token の表示/コピーは撤去 — セッションは httpOnly cookie に
				     一本化され JS から読めない。外部クライアント用の資格情報は API Key を使う。
				     鍵は ***** でマスクし目玉アイコンで切替(SensitiveInput) -->
				<div class="flex mt-2 items-center gap-1.5">
					{#if APIKey}
						<SensitiveInput
							outerClassName="flex flex-1 min-w-0 px-2 bg-surface-muted rounded-xs"
							inputClassName="w-full text-sm py-1 bg-transparent outline-hidden"
							value={APIKey}
							readOnly={true}
						/>

						<Tooltip content={$i18n.t('Copy')}>
							<button
								class="p-1.5 hover:bg-surface-hover rounded-lg"
								type="button"
								aria-label={$i18n.t('Copy')}
								onclick={() => {
									copyToClipboard(APIKey);
									APIKeyCopied = true;
									setTimeout(() => {
										APIKeyCopied = false;
									}, 2000);
								}}
							>
								{#if APIKeyCopied}
									<span class="i-lucide-check size-4 block"></span>
								{:else}
									<span class="i-lucide-copy size-4 block"></span>
								{/if}
							</button>
						</Tooltip>

						<Tooltip content={$i18n.t('Create new key')}>
							<button
								class="p-1.5 hover:bg-surface-hover rounded-lg"
								type="button"
								aria-label={$i18n.t('Create new key')}
								onclick={() => {
									createAPIKeyHandler();
								}}
							>
								<span class="i-lucide-refresh-cw size-4 block"></span>
							</button>
						</Tooltip>
					{:else}
						<button
							class="flex gap-1.5 items-center text-xs font-medium px-3.5 py-1.5 rounded-full bg-secondary hover:bg-secondary-hover text-secondary-content"
							type="button"
							onclick={() => {
								createAPIKeyHandler();
							}}
						>
							<span class="i-lucide-plus size-3.5 block"></span>

							{$i18n.t('Create new secret key')}</button
						>
					{/if}
				</div>
			</div>
		{/if}
	</div>

	<div class="flex justify-end pt-3 text-sm font-medium">
		<button
			class="px-3.5 py-1.5 text-sm font-medium bg-primary hover:bg-primary-hover text-primary-content rounded-full"
			type="button"
			onclick={async () => {
				const res = await submitHandler();

				if (res) {
					saveHandler();
				}
			}}
		>
			{$i18n.t('Save')}
		</button>
	</div>
</div>
