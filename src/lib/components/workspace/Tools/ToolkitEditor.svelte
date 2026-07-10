<script>
	import { getContext, onMount, tick } from 'svelte';

	const i18n = getContext('i18n');

	import { toast } from 'svelte-sonner';
	import CodeEditor from '$lib/components/common/CodeEditor.svelte';
	import Textarea from '$lib/components/common/Textarea.svelte';
	import { goto } from '$app/navigation';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Badge from '$lib/components/common/Badge.svelte';
	import ChevronLeft from '$lib/components/icons/ChevronLeft.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import LockClosed from '$lib/components/icons/LockClosed.svelte';
	import AccessControlModal from '../common/AccessControlModal.svelte';
	import { user } from '$lib/stores';

	let formElement = null;
	let loading = false;

	// llens(#43 Phase2b): id リネームは「旧 id 削除 → 新 id 作成」で差し替える(PK 不変)。
	let showRenameConfirm = false;
	let showAccessControlModal = false;

	export let edit = false;

	export let onSave = () => {};

	export let id = '';
	export let name = '';
	// llens(#43): description は top-level フィールド(旧 meta.description)。
	export let description = '';
	export let content = '';
	export let accessControl = {};

	let _content = '';
	// 編集開始時の id を控え、変更があれば確認ダイアログを出す。
	let originalId = id;

	$: if (content) {
		updateContent();
	}

	const updateContent = () => {
		_content = content;
	};

	$: if (name && !edit) {
		id = name.replace(/\s+/g, '_').toLowerCase();
	}

	let codeEditor;
	let boilerplate = `import os
import requests
from datetime import datetime
from pydantic import BaseModel, Field

class Tools:
    def __init__(self):
        pass

    # Add your custom tools using pure Python code here, make sure to add type hints and descriptions
	
    def get_user_name_and_email_and_id(self, __user__: dict = {}) -> str:
        """
        Get the user name, Email and ID from the user object.
        """

        # Do not include a descrption for __user__ as it should not be shown in the tool's specification
        # The session user object will be passed as a parameter when the function is called

        print(__user__)
        result = ""

        if "name" in __user__:
            result += f"User: {__user__['name']}"
        if "id" in __user__:
            result += f" (ID: {__user__['id']})"
        if "email" in __user__:
            result += f" (Email: {__user__['email']})"

        if result == "":
            result = "User: Unknown"

        return result

    def get_current_time(self) -> str:
        """
        Get the current time in a more human-readable format.
        """

        now = datetime.now()
        current_time = now.strftime("%I:%M:%S %p")  # Using 12-hour format with AM/PM
        current_date = now.strftime(
            "%A, %B %d, %Y"
        )  # Full weekday, month name, day, and year

        return f"Current Date and Time = {current_date}, {current_time}"

    def calculator(
        self,
        equation: str = Field(
            ..., description="The mathematical equation to calculate."
        ),
    ) -> str:
        """
        Calculate the result of an equation.
        """

        # Avoid using eval in production code
        # https://nedbatchelder.com/blog/201206/eval_really_is_dangerous.html
        try:
            result = eval(equation)
            return f"{equation} = {result}"
        except Exception as e:
            print(e)
            return "Invalid equation"

    def get_current_weather(
        self,
        city: str = Field(
            "New York, NY", description="Get the current weather for a given city."
        ),
    ) -> str:
        """
        Get the current weather for a given city.
        """

        api_key = os.getenv("OPENWEATHER_API_KEY")
        if not api_key:
            return (
                "API key is not set in the environment variable 'OPENWEATHER_API_KEY'."
            )

        base_url = "http://api.openweathermap.org/data/2.5/weather"
        params = {
            "q": city,
            "appid": api_key,
            "units": "metric",  # Optional: Use 'imperial' for Fahrenheit
        }

        try:
            response = requests.get(base_url, params=params)
            response.raise_for_status()  # Raise HTTPError for bad responses (4xx and 5xx)
            data = response.json()

            if data.get("cod") != 200:
                return f"Error fetching weather data: {data.get('message')}"

            weather_description = data["weather"][0]["description"]
            temperature = data["main"]["temp"]
            humidity = data["main"]["humidity"]
            wind_speed = data["wind"]["speed"]

            return f"Weather in {city}: {temperature}°C"
        except requests.RequestException as e:
            return f"Error fetching weather data: {str(e)}"
`;

	const saveHandler = async () => {
		loading = true;
		onSave({
			id,
			name,
			description,
			content,
			access_control: accessControl
		});
	};

	const submitHandler = async () => {
		if (!codeEditor) return;

		content = _content;
		await tick();

		const res = await codeEditor.formatPythonCodeHandler();
		await tick();

		content = _content;
		await tick();

		if (!res) return;

		// 編集で id が変わったら、割り当て・valves がちぎれる旨を確認してから差し替える。
		if (edit && id !== originalId) {
			// id 差し替えは create 経由 = description 必須(#43 P1)。空だと create が 400 で
			// 無言失敗するため、confirm を出す前に明示的に弾く。
			if ((description ?? '').trim() === '') {
				toast.error($i18n.t('A description is required to change the ID.'));
				return;
			}
			showRenameConfirm = true;
		} else {
			saveHandler();
		}
	};
</script>

<AccessControlModal
	bind:show={showAccessControlModal}
	bind:accessControl
	allowPublic={true}
/>

<div class=" flex flex-col justify-between w-full overflow-y-auto h-full">
	<div class="mx-auto w-full md:px-0 h-full">
		<form
			bind:this={formElement}
			class=" flex flex-col max-h-[100dvh] h-full"
			on:submit|preventDefault={() => {
				submitHandler();
			}}
		>
			<div class="flex flex-col flex-1 overflow-auto h-0 rounded-lg">
				<!-- llens(#44): 作成/編集画面を skill 型のセクション構成に統一 -->
				<div class="w-full mb-2 flex flex-col gap-1">
					<div class="flex w-full items-center">
						<div class=" shrink-0 mr-2">
							<Tooltip content={$i18n.t('Back')}>
								<button
									class="w-full text-left text-sm py-1.5 px-1 rounded-lg dark:text-gray-300 dark:hover:text-white hover:bg-black/5 dark:hover:bg-gray-850"
									on:click={() => {
										goto('/workspace/tools');
									}}
									type="button"
								>
									<ChevronLeft strokeWidth="2.5" />
								</button>
							</Tooltip>
						</div>

						<div class="flex-1">
							<input
								class="w-full text-2xl font-semibold bg-transparent outline-hidden font-primary"
								type="text"
								placeholder={$i18n.t('Tool Name')}
								bind:value={name}
								required
							/>
						</div>

						<div class="self-center shrink-0">
							<button
								class="bg-gray-50 hover:bg-gray-100 text-black dark:bg-gray-850 dark:hover:bg-gray-800 dark:text-white transition px-2 py-1 rounded-full flex gap-1 items-center"
								type="button"
								on:click={() => {
									showAccessControlModal = true;
								}}
							>
								<LockClosed strokeWidth="2.5" className="size-3.5" />

								<div class="text-sm font-medium shrink-0">
									{$i18n.t('Access')}
								</div>
							</button>
						</div>
					</div>

					<div class="flex gap-0.5 items-center text-xs text-gray-500 px-1">
						<input
							class="w-full bg-transparent outline-hidden"
							type="text"
							placeholder={$i18n.t('Tool ID')}
							bind:value={id}
							required
						/>
					</div>
				</div>

				<div class="my-2">
					<div class=" self-center text-sm font-semibold mb-1">{$i18n.t('Description')}</div>
					<Textarea
						className="text-sm w-full bg-transparent outline-hidden overflow-y-hidden resize-none"
						placeholder={$i18n.t('e.g. Tools for performing various operations')}
						bind:value={description}
						rows={2}
					/>
				</div>

				<div class=" self-start text-sm font-semibold px-1 mb-1">{$i18n.t('Tool Content')}</div>
				<div class="mb-2 flex-1 overflow-auto h-0 rounded-lg">
					<CodeEditor
						bind:this={codeEditor}
						value={content}
						lang="python"
						{boilerplate}
						onChange={(e) => {
							_content = e;
						}}
						onSave={async () => {
							if (formElement) {
								formElement.requestSubmit();
							}
						}}
					/>
				</div>

				<div class="pb-3 flex justify-start">
					<button
						class="px-4 py-2 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-lg"
						type="submit"
					>
						{$i18n.t('Save')}
					</button>
				</div>
			</div>
		</form>
	</div>
</div>

<ConfirmDialog
	bind:show={showRenameConfirm}
	title={$i18n.t('Change ID?')}
	on:confirm={() => {
		saveHandler();
	}}
>
	<div class="text-sm text-gray-500">
		<div class=" bg-yellow-500/20 text-yellow-700 dark:text-yellow-200 rounded-lg px-4 py-3">
			<div>
				{$i18n.t('The ID will be changed by deleting the old one and creating a new one.')}
			</div>
			<ul class=" mt-1 list-disc pl-4 text-xs">
				<li>{$i18n.t('Assignments and references from other items will be detached.')}</li>
				<li>{$i18n.t('Per-item settings such as valves will be reset.')}</li>
			</ul>
		</div>
	</div>
</ConfirmDialog>
