<script lang="ts">
	import { getContext, tick } from 'svelte';

	import CodeEditor from '$lib/components/common/CodeEditor.svelte';
	import Textarea from '$lib/components/common/Textarea.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import AccessControlModal from '$lib/components/workspace/common/AccessControlModal.svelte';

	const i18n = getContext('i18n');

	let {
		edit = false,
		onSave = () => {},
		id = $bindable(''),
		name = $bindable(''),
		// llens(#43): description は top-level フィールド(旧 meta.description)。空でも可(specs の docstring が真実源)。
		description = $bindable(''),
		content = $bindable(''),
		accessControl = $bindable({})
	}: {
		edit?: boolean;
		onSave?: (data: any) => void;
		id?: string;
		name?: string;
		description?: string;
		content?: string;
		accessControl?: any;
	} = $props();

	let formElement = $state<HTMLFormElement | null>(null);
	let codeEditor = $state<any>(null);

	// llens(#43 Phase2b): id リネームは「旧 id 削除 → 新 id 作成」で差し替える(PK 不変)。
	// 編集開始時の id を控え、変更があれば確認ダイアログを出す。
	let originalId = id;
	let showRenameConfirm = $state(false);
	let showAccessControlModal = $state(false);

	let _content = $state('');

	$effect(() => {
		if (content) {
			_content = content;
		}
	});

	// 作成時は表示名から id を自動生成する
	$effect(() => {
		if (name && !edit) {
			id = name.replace(/\s+/g, '_').toLowerCase();
		}
	});

	const boilerplate = `import os
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
        current_time = now.strftime("%I:%M:%S %p") # Using 12-hour format with AM/PM
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
 "units": "metric", # Optional: Use 'imperial' for Fahrenheit
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
			showRenameConfirm = true;
		} else {
			saveHandler();
		}
	};
</script>

<AccessControlModal bind:show={showAccessControlModal} bind:accessControl allowPublic={true} />

<!-- 高さは workspace layout(.max-w-5xl の h-full)から一段で貼る(FunctionEditor と同型)。 -->
<form
	bind:this={formElement}
	class="flex flex-col h-full"
	onsubmit={(e) => {
		e.preventDefault();
		submitHandler();
	}}
>
	<div>
		<!-- llens(#44): 作成/編集画面を function 型のセクション構成に統一。
		 ラベルは固定幅(w-24)でコントロールと同一行 — エディタの縦を稼ぐ。 -->
		<a
			class="inline-flex items-center text-sm py-0.5 text-content-subtle hover:text-content font-medium"
			href="/workspace/tools"
		>
			<span class="i-lucide-arrow-left size-4 mr-2"></span>
			{$i18n.t('Back to list')}
		</a>

		<div class="flex items-center gap-2 my-2">
			<label class="w-24 shrink-0 text-sm font-semibold" for="tool-name">
				{$i18n.t('Name')}
			</label>
			<input
				id="tool-name"
				class="flex-1 min-w-0 text-sm py-1 px-2 bg-surface-raised rounded-xs outline-hidden font-primary"
				type="text"
				placeholder={$i18n.t('Tool Name')}
				bind:value={name}
				required
			/>

			<button
				class="shrink-0 inline-flex items-center gap-1 px-2 py-1 rounded-full bg-secondary hover:bg-secondary-hover text-secondary-content text-sm font-medium"
				type="button"
				onclick={() => {
					showAccessControlModal = true;
				}}
			>
				<span class="i-lucide-lock size-3.5 block"></span>
				{$i18n.t('Access')}
			</button>
		</div>

		<div class="flex items-center gap-2 my-2">
			<label class="w-24 shrink-0 text-sm font-semibold" for="tool-id">
				{$i18n.t('ID')}
			</label>
			<input
				id="tool-id"
				class="flex-1 min-w-0 px-2 py-1 text-sm bg-surface-raised rounded-xs outline-hidden"
				type="text"
				placeholder={$i18n.t('Tool ID')}
				bind:value={id}
				required
			/>
		</div>

		<div class="flex items-start gap-2 my-2">
			<label class="w-24 shrink-0 text-sm font-semibold pt-1" for="tool-description">
				{$i18n.t('Description')}
			</label>
			<Textarea
				id="tool-description"
				className="flex-1 min-w-0 px-2 py-1 text-sm bg-surface-raised rounded-xs outline-hidden overflow-y-hidden resize-none"
				placeholder={$i18n.t('e.g. Tools for performing various operations')}
				bind:value={description}
				rows={2}
			/>
		</div>

		<div class="text-sm font-semibold px-1 mb-1">{$i18n.t('Tool Content')}</div>
	</div>

	<div class="flex-1 min-h-0 mb-2 rounded-lg overflow-auto">
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
			class="px-4 py-2 text-sm font-medium bg-primary hover:bg-primary-hover text-primary-content rounded-lg"
			type="submit"
		>
			{$i18n.t('Save')}
		</button>
	</div>
</form>

<ConfirmDialog
	bind:show={showRenameConfirm}
	title={$i18n.t('Change ID?')}
	onConfirm={() => {
		saveHandler();
	}}
>
	<div class="text-sm text-content-muted">
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
