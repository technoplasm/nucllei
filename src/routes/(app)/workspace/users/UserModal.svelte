<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { getContext } from 'svelte';

	import { addUser } from '$lib/apis/auths';
	import { updateUserById, updateUserRole } from '$lib/apis/users';
	import { WEBUI_BASE_URL } from '$lib/constants';

	import Modal from '$lib/components/common/Modal.svelte';

	const i18n = getContext('i18n');

	let {
		show = $bindable(false),
		// llens(#55): selectedUser があれば編集モード、null なら作成モード(単一モーダル)
		selectedUser = null,
		sessionUser = null,
		// 宣言済みの追加 meta キー([{key, label}])。入力欄と保存経路が自動で生える
		metaFields = [],
		onSave = () => {}
	}: {
		show?: boolean;
		selectedUser?: any;
		sessionUser?: any;
		metaFields?: { key: string; label: string }[];
		onSave?: () => void;
	} = $props();

	let loading = $state(false);
	let tab = $state('');
	let inputFiles = $state<FileList | null>(null);

	let _user = $state({
		name: '',
		email: '',
		password: '',
		role: 'user',
		info: {} as Record<string, string>
	});

	const edit = $derived(selectedUser !== null);

	// llens: 開くたびに selectedUser から編集バッファを作り直す(show の変化検知でガード —
	// 素の「show が真なら再構築」はキーストロークごとに再実行され入力が巻き戻る)。
	let lastShow = false;
	$effect(() => {
		if (show === lastShow) return;
		lastShow = show;
		if (show) {
			tab = '';
			_user = {
				name: selectedUser?.name ?? '',
				email: selectedUser?.email ?? '',
				password: '',
				role: selectedUser?.role ?? 'user',
				info: Object.fromEntries(
					metaFields.map((field) => [field.key, selectedUser?.info?.[field.key] ?? ''])
				)
			};
		}
	});

	const editHandler = async () => {
		if (_user.role !== selectedUser.role) {
			const roleRes = await updateUserRole(selectedUser.id, _user.role).catch((error) => {
				toast.error(`${error}`);
				return null;
			});
			if (!roleRes) {
				return null;
			}
		}

		return await updateUserById(selectedUser.id, {
			name: _user.name,
			email: _user.email,
			password: _user.password,
			profile_image_url: selectedUser.profile_image_url,
			info: _user.info
		}).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
	};

	const submitHandler = async () => {
		const stopLoading = () => {
			onSave();
			loading = false;
		};

		if (tab === '') {
			loading = true;

			const res = edit
				? await editHandler()
				: await addUser(_user.name, _user.email, _user.password, _user.role, _user.info).catch(
						(error) => {
							toast.error(`${error}`);
						}
					);

			if (res) {
				stopLoading();
				show = false;
			}
		} else {
			if (inputFiles) {
				loading = true;

				const file = inputFiles[0];
				const reader = new FileReader();

				reader.onload = async (e) => {
					const csv = (e.target?.result ?? '') as string;
					const rows = csv.split('\n');

					let userCount = 0;

					for (const [idx, row] of rows.entries()) {
						const columns = row.split(',').map((col) => col.trim());

						if (idx > 0) {
							if (
								columns.length === 4 &&
								['admin', 'user', 'pending'].includes(columns[3].toLowerCase())
							) {
								const res = await addUser(
									columns[0],
									columns[1],
									columns[2],
									columns[3].toLowerCase()
								).catch((error) => {
									toast.error(`Row ${idx + 1}: ${error}`);
									return null;
								});

								if (res) {
									userCount = userCount + 1;
								}
							} else {
								toast.error(`Row ${idx + 1}: invalid format.`);
							}
						}
					}

					toast.success(`Successfully imported ${userCount} users.`);
					inputFiles = null;
					const uploadInputElement = document.getElementById(
						'upload-user-csv-input'
					) as HTMLInputElement | null;

					if (uploadInputElement) {
						uploadInputElement.value = '';
					}

					stopLoading();
				};

				reader.readAsText(file);
			} else {
				toast.error($i18n.t('File not found.'));
			}
		}

		loading = false;
	};
</script>

<Modal size="sm" bind:show>
	<div>
		<div class=" flex justify-between text-content px-5 pt-4 pb-2">
			<div class=" text-lg font-medium self-center">
				{edit ? $i18n.t('Edit User') : $i18n.t('Add User')}
			</div>
			<button
				class="self-center"
				aria-label={$i18n.t('Close')}
				onclick={() => {
					show = false;
				}}
			>
				<span class="i-lucide-x size-5 block"></span>
			</button>
		</div>

		<div class="flex flex-col md:flex-row w-full px-4 pb-3 md:space-x-4 text-content">
			<div class=" flex flex-col w-full sm:flex-row sm:justify-center sm:space-x-6">
				<form
					class="flex flex-col w-full"
					onsubmit={(e) => {
						e.preventDefault();
						submitHandler();
					}}
				>
					{#if !edit}
						<div
							class="flex -mt-2 mb-1.5 gap-1 scrollbar-none overflow-x-auto w-fit text-center text-sm font-medium rounded-full bg-transparent text-content"
						>
							<button
								class="min-w-fit rounded-full p-1.5 {tab === ''
									? ''
									: 'text-content-subtle hover:text-content'} transition"
								type="button"
								onclick={() => {
									tab = '';
								}}>{$i18n.t('Form')}</button
							>

							<button
								class="min-w-fit rounded-full p-1.5 {tab === 'import'
									? ''
									: 'text-content-subtle hover:text-content'} transition"
								type="button"
								onclick={() => {
									tab = 'import';
								}}>{$i18n.t('CSV Import')}</button
							>
						</div>
					{/if}

					<div class="px-1">
						{#if tab === ''}
							<div class="flex flex-col w-full mb-3">
								<div class=" mb-1 text-xs text-content-muted">{$i18n.t('Role')}</div>

								<div class="flex-1">
									<select
										class="w-full capitalize rounded-lg py-2 px-3 text-sm bg-gray-50 dark:bg-gray-850 disabled:text-content-subtle outline-hidden"
										bind:value={_user.role}
										disabled={edit && selectedUser?.id === sessionUser?.id}
										required
									>
										<option value="pending"> {$i18n.t('pending')} </option>
										<option value="user"> {$i18n.t('user')} </option>
										<option value="admin"> {$i18n.t('admin')} </option>
									</select>
								</div>
							</div>

							<div class="flex flex-col w-full mt-1">
								<div class=" mb-1 text-xs text-content-muted">{$i18n.t('Name')}</div>

								<div class="flex-1">
									<input
										class="w-full rounded-lg py-2 px-3 text-sm bg-gray-50 dark:bg-gray-850 disabled:text-content-subtle outline-hidden"
										type="text"
										bind:value={_user.name}
										placeholder={$i18n.t('Enter Your Full Name')}
										autocomplete="off"
										required
									/>
								</div>
							</div>

							<hr class=" border-line my-2.5 w-full" />

							<div class="flex flex-col w-full">
								<div class=" mb-1 text-xs text-content-muted">{$i18n.t('Email')}</div>

								<div class="flex-1">
									<input
										class="w-full rounded-lg py-2 px-3 text-sm bg-gray-50 dark:bg-gray-850 disabled:text-content-subtle outline-hidden"
										type="email"
										bind:value={_user.email}
										placeholder={$i18n.t('Enter Your Email')}
										disabled={edit && selectedUser?.id === sessionUser?.id}
										required
									/>
								</div>
							</div>

							<div class="flex flex-col w-full mt-1">
								<div class=" mb-1 text-xs text-content-muted">
									{edit ? $i18n.t('New Password') : $i18n.t('Password')}
								</div>

								<div class="flex-1">
									<input
										class="w-full rounded-lg py-2 px-3 text-sm bg-gray-50 dark:bg-gray-850 disabled:text-content-subtle outline-hidden"
										type="password"
										bind:value={_user.password}
										placeholder={$i18n.t('Enter Your Password')}
										autocomplete="new-password"
									/>
								</div>
							</div>

							{#if metaFields.length > 0}
								<hr class=" border-line my-2.5 w-full" />

								{#each metaFields as field (field.key)}
									<div class="flex flex-col w-full mt-1">
										<div class=" mb-1 text-xs text-content-muted">{field.label}</div>

										<div class="flex-1">
											<input
												class="w-full rounded-lg py-2 px-3 text-sm bg-gray-50 dark:bg-gray-850 disabled:text-content-subtle outline-hidden"
												type="text"
												bind:value={_user.info[field.key]}
												autocomplete="off"
											/>
										</div>
									</div>
								{/each}
							{/if}
						{:else if tab === 'import'}
							<div>
								<div class="mb-3 w-full">
									<input
										id="upload-user-csv-input"
										hidden
										bind:files={inputFiles}
										type="file"
										accept=".csv"
									/>

									<button
										class="w-full text-sm font-medium py-3 bg-transparent hover:bg-gray-100 border border-dashed dark:border-gray-850 dark:hover:bg-gray-850 text-center rounded-xl"
										type="button"
										onclick={() => {
											document.getElementById('upload-user-csv-input')?.click();
										}}
									>
										{#if inputFiles}
											{inputFiles.length > 0 ? `${inputFiles.length}` : ''} document(s) selected.
										{:else}
											{$i18n.t('Click here to select a csv file.')}
										{/if}
									</button>
								</div>

								<div class=" text-xs text-content-muted">
									ⓘ {$i18n.t(
										'Ensure your CSV file includes 4 columns in this order: Name, Email, Password, Role.'
									)}
									<a class="underline text-content" href="{WEBUI_BASE_URL}/static/user-import.csv">
										{$i18n.t('Click here to download user import template file.')}
									</a>
								</div>
							</div>
						{/if}
					</div>

					<div class="flex justify-end pt-3 text-sm font-medium">
						<button
							class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full flex flex-row space-x-1 items-center {loading
								? ' cursor-not-allowed'
								: ''}"
							type="submit"
							disabled={loading}
						>
							{$i18n.t('Save')}

							{#if loading}
								<span class="i-lucide-loader-circle size-4 block animate-spin ml-2 self-center"
								></span>
							{/if}
						</button>
					</div>
				</form>
			</div>
		</div>
	</div>
</Modal>
