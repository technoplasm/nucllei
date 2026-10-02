/**
 * チャットの送信側(#70 C トラック: Chat.svelte 分割)。
 *
 * 入力の検証と user/assistant メッセージ樹の組み立て、POST /api/chat/completions
 * (wire v2: 応答は SSE — 消費開始まで)と停止(stop)、再生成・続行・ペア作成の
 * ユーザー起点操作を持つ。イベントの適用は ChatEvents(登録済みハンドラ)側。
 * 状態は ChatSession を介して共有する。
 */
import { tick } from 'svelte';
import { get } from 'svelte/store';
import { toast } from 'svelte-sonner';
import { v4 as uuidv4 } from 'uuid';

import { chatId, chats, config, currentChatPage, models, settings, user } from '$lib/stores';
import type { Model } from '$lib/stores';
import { createMessagesList, promptTemplate, getPromptVariables } from '$lib/utils';
import { buildWireMessages, parseStopSequences } from '$lib/utils/chat-messages';
import { consumeCompletionStream, getActiveTaskIds } from '$lib/utils/completion-streams';
import type { ChatFile, ChatHistory, ChatMessage } from '$lib/types/chat';
import { getChatList } from '$lib/apis/chats';
import { generateOpenAIChatCompletion } from '$lib/apis/openai';
import { stopTask } from '$lib/apis';
import { WEBUI_BASE_URL } from '$lib/constants';

import { type ChatSession, type Translate } from './session.svelte';
import { type ChatEvents } from './events';

export class ChatSend {
	#session: ChatSession;
	#events: ChatEvents;
	#t: Translate;
	#eventTarget: EventTarget;
	#scrollToBottom: () => Promise<void> | void;

	// llens: task 生成前(モデル接続待ち)の completion fetch を stop で中断するための
	// AbortController 群(複数モデル並列送信に対応して配列)。task 生成後の中断は
	// stopTask(サーバ側 cancel)が担う。
	#completionAbortControllers: AbortController[] = [];

	constructor({
		session,
		events,
		t,
		eventTarget,
		scrollToBottom
	}: {
		session: ChatSession;
		events: ChatEvents;
		t: Translate;
		eventTarget: EventTarget;
		scrollToBottom: () => Promise<void> | void;
	}) {
		this.#session = session;
		this.#events = events;
		this.#t = t;
		this.#eventTarget = eventTarget;
		this.#scrollToBottom = scrollToBottom;
	}

	submitPrompt = async (userPrompt: string, { _raw = false } = {}) => {
		const session = this.#session;
		const messages: ChatMessage[] = createMessagesList(session.history, session.history.currentId);
		const lastMessage = messages.at(-1);
		const _selectedModels = session.selectedModels.map((modelId) =>
			get(models)
				.map((m) => m.id)
				.includes(modelId)
				? modelId
				: ''
		);
		if (JSON.stringify(session.selectedModels) !== JSON.stringify(_selectedModels)) {
			session.selectedModels = _selectedModels;
		}

		if (userPrompt === '' && session.files.length === 0) {
			toast.error(this.#t('Please enter a prompt'));
			return;
		}
		if (session.selectedModels.includes('')) {
			toast.error(this.#t('Model not selected'));
			return;
		}

		if (lastMessage && lastMessage.done != true) {
			// Response not done
			return;
		}
		if (lastMessage && lastMessage.error && !lastMessage.content) {
			// Error in response
			toast.error(this.#t(`Oops! There was an error in the previous response.`));
			return;
		}
		// llens(#69): 画像も file 化しアップロード経路に乗ったため、type を問わず uploading を待つ
		if (
			session.files.length > 0 &&
			session.files.filter((file) => file.status === 'uploading').length > 0
		) {
			toast.error(
				this.#t(`Oops! There are files still uploading. Please wait for the upload to complete.`)
			);
			return;
		}
		const maxFileCount = get(config)?.file?.max_count ?? null;
		if (maxFileCount !== null && session.files.length + session.chatFiles.length > maxFileCount) {
			toast.error(
				this.#t(`You can only chat with a maximum of {{maxCount}} file(s) at a time.`, {
					maxCount: maxFileCount
				})
			);
			return;
		}

		session.prompt = '';

		// Reset chat input textarea
		const chatInputElement = document.getElementById('chat-input');

		if (chatInputElement) {
			await tick();
			chatInputElement.style.height = '';
		}

		const _files: ChatFile[] = JSON.parse(JSON.stringify(session.files));
		session.chatFiles.push(..._files.filter((item) => ['file'].includes(item.type)));
		session.chatFiles = session.chatFiles.filter(
			// Remove duplicates — id を持つエントリは file.id で一意化(#69: 再添付は
			// メタ差分で JSON 全体比較をすり抜け、全文 context が二重注入される)。
			// id なし(旧形式)は従来の JSON 比較にフォールバック。
			(item, index, array) =>
				array.findIndex((i) =>
					item.id ? i.id === item.id : JSON.stringify(i) === JSON.stringify(item)
				) === index
		);

		session.files = [];
		session.prompt = '';

		// Create user message
		const userMessageId = uuidv4();
		const userMessage = {
			id: userMessageId,
			parentId: lastMessage ? lastMessage.id : null,
			childrenIds: [],
			role: 'user',
			content: userPrompt,
			files: _files.length > 0 ? _files : undefined,
			timestamp: Math.floor(Date.now() / 1000) // Unix epoch
		};

		// Add message to history and Set currentId to messageId
		session.history.messages[userMessageId] = userMessage;
		session.history.currentId = userMessageId;

		// Append messageId to childrenIds of parent message
		if (lastMessage) {
			session.history.messages[lastMessage.id].childrenIds.push(userMessageId);
		}

		// focus on chat input
		const chatInput = document.getElementById('chat-input');
		chatInput?.focus();

		session.saveSessionSelectedModels();

		await this.sendPrompt(session.history, userPrompt, userMessageId, { newChat: true });
	};

	// llens(#108): マルチモデル送信は仕様ごと撤去 — 対象は常に 1 モデル。
	// modelId 指定(再生成)が無ければ選択中モデルを使う。
	sendPrompt = async (
		_history: ChatHistory,
		_prompt: string,
		parentId: string,
		{ modelId = null, newChat = false }: { modelId?: string | null; newChat?: boolean } = {}
	) => {
		const session = this.#session;

		if (session.autoScroll) {
			this.#scrollToBottom();
		}

		let _chatId = get(chatId);
		_history = JSON.parse(JSON.stringify(_history));

		const targetModelId = modelId ?? session.selectedModelIds[0];
		const model = get(models).find((m) => m.id === targetModelId);
		if (!model) {
			toast.error(this.#t(`Model {{modelId}} not found`, { modelId: targetModelId }));
			return;
		}

		const responseMessageId = uuidv4();
		const responseMessage = {
			parentId: parentId,
			id: responseMessageId,
			childrenIds: [],
			role: 'assistant',
			content: '',
			model: model.id,
			modelName: model.name ?? model.id,
			userContext: null,
			timestamp: Math.floor(Date.now() / 1000) // Unix epoch
		};

		// Add message to history and Set currentId to messageId
		session.history.messages[responseMessageId] = responseMessage;
		session.history.currentId = responseMessageId;

		// Append messageId to childrenIds of parent message
		if (parentId !== null && session.history.messages[parentId]) {
			// Add null check before accessing childrenIds
			session.history.messages[parentId].childrenIds = [
				...session.history.messages[parentId].childrenIds,
				responseMessageId
			];
		}

		// Create new chat if newChat is true and first user message
		if (
			newChat &&
			_history.currentId !== null &&
			_history.messages[_history.currentId].parentId === null
		) {
			_chatId = await session.initChatHandler(_history);
		}

		await tick();

		_history = JSON.parse(JSON.stringify(session.history));
		// Save chat after all messages have been created
		// llens(#110): BE のサーバ駆動配達(通知ターン append)と競合した 400 は
		// saveChatHandler が接ぎ直しリカバリする。接ぎ直しが起きたら wire は
		// merged で組み直す(通知メッセージをモデルに載せる)。恒久失敗は送信中止
		const saveRes = await session.saveChatHandler(_chatId, _history);
		if (!saveRes.ok) {
			if (session.prompt === '') {
				session.prompt = _prompt;
			}
			toast.error(this.#t('Failed to save the chat. Your message was restored to the input.'));
			return;
		}
		if (saveRes.history) {
			_history = JSON.parse(JSON.stringify(saveRes.history));
		}

		const messages: ChatMessage[] = createMessagesList(_history, parentId);
		// If there are image files, check if model is vision capable
		const hasImages = messages.some((message) =>
			message.files?.some((file) => file.type === 'image')
		);

		if (hasImages && !(model.info?.meta?.capabilities?.vision ?? true)) {
			toast.error(
				this.#t('Model {{modelName}} is not vision capable', {
					modelName: model.name ?? model.id
				})
			);
		}

		// llens(#63): 個人化(Memory)は機能ごと廃止(テーブルは温存)。
		// userContext 注入は行わない。
		_history.messages[responseMessageId].userContext = null;

		this.#scrollToBottom();
		await this.#sendChatCompletionRequest(_history, model, responseMessageId, _chatId);

		currentChatPage.set(1);
		chats.set(await getChatList(get(currentChatPage)));
	};

	// wire v2: POST /api/chat/completions の応答は SSE。ここでは送信と消費開始までを
	// 担い、イベントの適用は registerChatHandler 済みの chatEventHandler が行う。
	#sendChatCompletionRequest = async (
		_history: ChatHistory,
		model: Model,
		responseMessageId: string,
		_chatId: string
	) => {
		const session = this.#session;

		const responseMessage = _history.messages[responseMessageId];
		const userMessage =
			responseMessage.parentId !== null ? _history.messages[responseMessage.parentId] : undefined;

		let files: ChatFile[] = JSON.parse(JSON.stringify(session.chatFiles));
		files.push(...(userMessage?.files ?? []).filter((item) => ['file'].includes(item.type)));
		// Remove duplicates
		files = files.filter(
			(item, index, array) =>
				array.findIndex((i) => JSON.stringify(i) === JSON.stringify(item)) === index
		);

		this.#scrollToBottom();
		this.#eventTarget.dispatchEvent(
			new CustomEvent('chat:start', {
				detail: {
					id: responseMessageId
				}
			})
		);
		await tick();

		const stream =
			model?.info?.params?.stream_response ??
			get(settings)?.params?.stream_response ??
			session.params?.stream_response ??
			true;

		// wire 形への組み立て(system 注入・画像展開・tool 往復復元)は純関数層
		// (chat-messages.ts buildWireMessages)— vitest で契約を固定している
		const messages = buildWireMessages(
			createMessagesList(_history, responseMessageId),
			session.params?.system
				? promptTemplate(
						session.params?.system ?? '',
						get(user)?.name,
						undefined,
						model.info?.base_model_id || model.id
					)
				: null
		);

		const abortController = new AbortController();
		this.#completionAbortControllers.push(abortController);

		const res = await generateOpenAIChatCompletion(
			{
				stream: stream,
				model: model.id,
				messages: messages,
				params: {
					...get(settings)?.params,
					...session.params,

					format: get(settings).requestFormat ?? undefined,
					keep_alive: get(settings).keepAlive ?? undefined,
					stop: parseStopSequences(session.params?.stop ?? get(settings)?.params?.stop)
				},

				files: (files?.length ?? 0) > 0 ? files : undefined,
				// llens(#54): tool はモデル割り当て一本 — tool_ids はサーバー側解決に移行し送らない
				// llens(#70): features(image_generation)はネイティブ画像生成の BE 撤去に伴い送らない

				variables: {
					...getPromptVariables(get(user)?.name, undefined)
				},
				model_item: get(models).find((m) => m.id === model.id),

				chat_id: get(chatId),
				id: responseMessageId,

				...((messages.length == 1 ||
					(messages.length == 2 &&
						messages.at(0)?.role === 'system' &&
						messages.at(1)?.role === 'user')) &&
				(session.selectedModels[0] === model.id || session.atSelectedModel !== undefined)
					? {
							background_tasks: {
								title_generation: get(settings)?.title?.auto ?? true
							}
						}
					: {})
				// llens(#57/#70): stream_options.include_usage の注入は backend
				// (middleware の is_usage_requested — 既定 ON・capabilities.usage=False で opt-out)
				// に一元化。クライアント側の条件付き付与は撤去した。
			},
			`${WEBUI_BASE_URL}/api`,
			abortController.signal
		).catch(async (error) => {
			if (abortController.signal.aborted) {
				// stop によるユーザー中断 — エラーにしない(task-cancelled と同じ扱い)。
				// completed(outlet)へは下の !res 分岐から interrupted 付きで流れる
				responseMessage.done = true;
				responseMessage.interrupted = true;
			} else {
				// HTTP 応答エラー(payload 処理失敗 = 400 等)/ 接続エラー
				this.#handleCompletionRequestError(error, responseMessage);
			}

			session.history.messages[responseMessageId] = responseMessage;
			session.history.currentId = responseMessageId;
			return null;
		});

		this.#completionAbortControllers = this.#completionAbortControllers.filter(
			(c) => c !== abortController
		);

		if (res && res.body) {
			// wire v2: SSE を EOF まで消費する(await しない — fire-and-consume)。
			// イベントの適用は登録ハンドラ(Chat が unmount 済みなら +layout の
			// fallback)が担う。EOF がタスク終端(done の後にも chat:title が来る)。
			consumeCompletionStream(_chatId, res.body, {
				onTask: (taskId) => {
					session.taskIds = [...(session.taskIds ?? []), taskId];
				}
			}).catch((error) => {
				// 消費中の接続断。タスクはサーバー側で継続する(リロード/再訪で再アタッチ)
				console.error('completion stream disconnected', error);
				responseMessage.error = { content: `${error}` };
				session.history.messages[responseMessageId] = responseMessage;
			});
		} else {
			// llens: task 生成前の失敗(接続エラー等)は done イベントが来ないため、
			// ここから completed(outlet filter)を通す — error 付き message が
			// outlet に渡り、turn の失敗として記録できる
			await this.#events.chatCompletedHandler(
				_chatId,
				model.id,
				responseMessageId,
				createMessagesList(session.history, responseMessageId)
			);
		}

		await tick();
		this.#scrollToBottom();
	};

	// HTTP 応答エラー用(fetch catch / res.json 形)。イベント内エラーは
	// {error: {content}} の一形で chatCompletionEventHandler 側が処理する。
	#handleCompletionRequestError = (error: any, responseMessage: ChatMessage) => {
		console.error(error);
		const errorMessage = error?.detail ?? error?.error?.message ?? error?.message ?? `${error}`;
		toast.error(errorMessage);

		responseMessage.error = {
			content: this.#t(`Uh-oh! There was an issue with the response.`) + '\n' + errorMessage
		};
		responseMessage.done = true;

		this.#session.history.messages[responseMessage.id] = responseMessage;
	};

	stopResponse = async () => {
		const session = this.#session;

		// llens: task 生成前(モデル接続待ち)の fetch を先に落とす。これが無いと
		// stop 後に上流が応答した時点で task が生まれ、「中断済み」のはずのチャットへ
		// 応答が流れ込む
		for (const controller of this.#completionAbortControllers) {
			controller.abort();
		}
		this.#completionAbortControllers = [];

		// wire v2: 消費中ストリームの task も含めて止める(サーバー側 cancel)。
		// SSE 自体は task-cancelled → done → EOF の順で自然に閉じる
		const stopTaskIds = [
			...new Set([...(session.taskIds ?? []), ...getActiveTaskIds(get(chatId))])
		];

		if (stopTaskIds.length > 0) {
			for (const taskId of stopTaskIds) {
				await stopTask(taskId).catch((error) => {
					toast.error(`${error}`);
					return null;
				});
			}

			session.taskIds = null;

			if (session.history.currentId !== null) {
				const responseMessage = session.history.messages[session.history.currentId];
				// Set all response messages to done
				if (responseMessage.parentId !== null) {
					for (const messageId of session.history.messages[responseMessage.parentId].childrenIds) {
						session.history.messages[messageId].done = true;
					}
				}

				session.history.messages[session.history.currentId] = responseMessage;
			}

			if (session.autoScroll) {
				this.#scrollToBottom();
			}
		}
	};

	regenerateResponse = async (message: ChatMessage) => {
		const session = this.#session;

		if (session.history.currentId && message.parentId !== null) {
			const userMessage = session.history.messages[message.parentId];
			const userPrompt = userMessage.content;

			if (session.autoScroll) {
				this.#scrollToBottom();
			}

			await this.sendPrompt(session.history, userPrompt, userMessage.id);
		}
	};

	continueResponse = async () => {
		const session = this.#session;
		const _chatId = get(chatId);

		if (
			session.history.currentId &&
			session.history.messages[session.history.currentId].done == true
		) {
			const responseMessage = session.history.messages[session.history.currentId];
			responseMessage.done = false;
			await tick();

			const model = get(models)
				.filter((m) => m.id === (responseMessage?.selectedModelId ?? responseMessage.model))
				.at(0);

			if (model) {
				await this.#sendChatCompletionRequest(session.history, model, responseMessage.id, _chatId);
			}
		}
	};

	createMessagePair = async (userPrompt: string) => {
		const session = this.#session;

		session.prompt = '';
		if (session.selectedModels.length === 0) {
			toast.error(this.#t('Model not selected'));
		} else {
			const modelId = session.selectedModels[0];
			const model = get(models)
				.filter((m) => m.id === modelId)
				.at(0);

			const messages: ChatMessage[] = createMessagesList(
				session.history,
				session.history.currentId
			);
			const parentMessage = messages.length !== 0 ? messages.at(-1) : null;

			const userMessageId = uuidv4();
			const responseMessageId = uuidv4();

			const userMessage = {
				id: userMessageId,
				parentId: parentMessage ? parentMessage.id : null,
				childrenIds: [responseMessageId],
				role: 'user',
				content: userPrompt ? userPrompt : `[PROMPT] ${userMessageId}`,
				timestamp: Math.floor(Date.now() / 1000)
			};

			const responseMessage = {
				id: responseMessageId,
				parentId: userMessageId,
				childrenIds: [],
				role: 'assistant',
				content: `[RESPONSE] ${responseMessageId}`,
				done: true,

				model: modelId,
				// model は id 一致で引いているため model.id === modelId — fallback は同値
				modelName: model?.name ?? modelId,
				timestamp: Math.floor(Date.now() / 1000)
			};

			if (parentMessage) {
				parentMessage.childrenIds.push(userMessageId);
				session.history.messages[parentMessage.id] = parentMessage;
			}
			session.history.messages[userMessageId] = userMessage;
			session.history.messages[responseMessageId] = responseMessage;

			session.history.currentId = responseMessageId;

			await tick();

			if (session.autoScroll) {
				this.#scrollToBottom();
			}

			if (messages.length === 0) {
				await session.initChatHandler(session.history);
			} else {
				await session.saveChatHandler(get(chatId), session.history);
			}
		}
	};

	addMessages = async ({
		modelId,
		parentId,
		messages
	}: {
		modelId: string;
		parentId: string;
		messages: { role: string; content: string }[];
	}) => {
		const session = this.#session;
		const model = get(models)
			.filter((m) => m.id === modelId)
			.at(0);

		let parentMessage: ChatMessage | undefined = session.history.messages[parentId];
		let currentParentId = parentMessage ? parentMessage.id : null;
		for (const message of messages) {
			const messageId = uuidv4();

			if (message.role === 'user') {
				const userMessage = {
					id: messageId,
					parentId: currentParentId,
					childrenIds: [],
					timestamp: Math.floor(Date.now() / 1000),
					...message
				};

				if (parentMessage) {
					parentMessage.childrenIds.push(messageId);
					session.history.messages[parentMessage.id] = parentMessage;
				}

				session.history.messages[messageId] = userMessage;
				parentMessage = userMessage;
				currentParentId = messageId;
			} else {
				const responseMessage = {
					id: messageId,
					parentId: currentParentId,
					childrenIds: [],
					done: true,
					// model は id 一致で引いているため model.id === modelId — fallback は同値
					model: model?.id ?? modelId,
					modelName: model?.name ?? modelId,
					timestamp: Math.floor(Date.now() / 1000),
					...message
				};

				if (parentMessage) {
					parentMessage.childrenIds.push(messageId);
					session.history.messages[parentMessage.id] = parentMessage;
				}

				session.history.messages[messageId] = responseMessage;
				parentMessage = responseMessage;
				currentParentId = messageId;
			}
		}

		session.history.currentId = currentParentId;
		await tick();

		if (session.autoScroll) {
			this.#scrollToBottom();
		}

		if (messages.length === 0) {
			await session.initChatHandler(session.history);
		} else {
			await session.saveChatHandler(get(chatId), session.history);
		}
	};
}
