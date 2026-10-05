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

import { chatId, config, models, settings, user } from '$lib/stores';
import type { Model } from '$lib/stores';
import { createMessagesList, promptTemplate, getPromptVariables } from '$lib/utils';
import { buildWireMessages, parseStopSequences } from '$lib/utils/chat-messages';
import { stripLocalTurn } from '$lib/utils/history-graft';
import { mergeRequestFiles } from '$lib/utils/chat-files';
import { consumeCompletionStream, getActiveTaskIds } from '$lib/utils/completion-streams';
import type { ChatFile, ChatHistory, ChatMessage } from '$lib/types/chat';
import { generateOpenAIChatCompletion } from '$lib/apis/openai';
import { stopTask } from '$lib/apis';
import { WEBUI_BASE_URL } from '$lib/constants';
import { clearLiveUsage, markLiveUsagePending } from '$lib/stores/live-usage.svelte';

import { type ChatSession, type Translate } from './session.svelte';
import { type ChatEvents } from './events';

export class ChatSend {
	#session: ChatSession;
	#events: ChatEvents;
	#t: Translate;
	#eventTarget: EventTarget;
	#scrollToBottom: () => Promise<void> | void;
	// done 前に SSE が切れたことの通知(走行中ターンへの追いつきは Chat.svelte が持つ)
	#onStreamLost: (chatId: string, messageId: string) => void;

	// llens: task 生成前(モデル接続待ち)の completion fetch を stop で中断するための
	// AbortController 群(複数モデル並列送信に対応して配列)。task 生成後の中断は
	// stopTask(サーバ側 cancel)が担う。
	#completionAbortControllers: AbortController[] = [];

	// 停止の連打ガード。stopTask の応答待ちの間に次の押下が来ると、同じ task を
	// もう一度止めに行って(既に消えた task への)404 のトーストが出ていた
	#stopping = false;

	constructor({
		session,
		events,
		t,
		eventTarget,
		scrollToBottom,
		onStreamLost
	}: {
		session: ChatSession;
		events: ChatEvents;
		t: Translate;
		eventTarget: EventTarget;
		scrollToBottom: () => Promise<void> | void;
		onStreamLost?: (chatId: string, messageId: string) => void;
	}) {
		this.#session = session;
		this.#events = events;
		this.#t = t;
		this.#eventTarget = eventTarget;
		this.#scrollToBottom = scrollToBottom;
		this.#onStreamLost = onStreamLost ?? (() => {});
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
		// 送信前の保存に失敗したら添付の状態ごと送信前へ戻す(下で巻き戻しに使う)
		const chatFilesBefore: ChatFile[] = JSON.parse(JSON.stringify(session.chatFiles));
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

		const sent = await this.sendPrompt(session.history, userPrompt, userMessageId, {
			newChat: true
		});
		if (!sent) {
			// 送信前の保存に失敗して送信を取りやめた(送信対は sendPrompt が history から
			// 取り除き済み)。入力文と添付を入力欄へ戻す — 入力欄の onChange が下書き
			// (localStorage)も書き戻す。送信までに打ち始めた次の入力は上書きしない
			if (session.prompt === '') {
				session.prompt = userPrompt;
			}
			if (session.files.length === 0) {
				session.files = _files;
			}
			session.chatFiles = chatFilesBefore;
		}
	};

	// llens(#108): マルチモデル送信は仕様ごと撤去 — 対象は常に 1 モデル。
	// modelId 指定(再生成)が無ければ選択中モデルを使う。
	// 戻り値: 送信前の保存(chat 作成を含む)に失敗して送信を取りやめたら false。
	// そのとき送信対は history から取り除いてあり、入力欄への復元は呼び出し側が持つ
	sendPrompt = async (
		_history: ChatHistory,
		_prompt: string,
		parentId: string,
		{ modelId = null, newChat = false }: { modelId?: string | null; newChat?: boolean } = {}
	): Promise<boolean> => {
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
			// 保存前の中止(history は未変更)— 入力欄の復元対象ではない
			return true;
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

		// llens(#102): floating ゲージを送信の瞬間から「計測中」にする(最初の chat:completion
		// を待つと新規チャットで ~80ms ゲージが空になる)。新規チャットは chat 作成前で
		// _chatId が '' — '' スコープに付けて、作成後に実 id へ付け替える。外すのは
		// 最初の chat:usage / done(events)か、下の失敗・中断の各分岐
		markLiveUsagePending(_chatId, responseMessageId);

		// Append messageId to childrenIds of parent message
		if (parentId !== null && session.history.messages[parentId]) {
			// Add null check before accessing childrenIds
			session.history.messages[parentId].childrenIds = [
				...session.history.messages[parentId].childrenIds,
				responseMessageId
			];
		}

		// Create new chat if newChat is true and first user message
		const createsChat =
			newChat &&
			_history.currentId !== null &&
			_history.messages[_history.currentId].parentId === null;
		const draftChatId = _chatId;

		// Save chat after all messages have been created
		// llens(#110): BE のサーバ駆動配達(通知ターン append)と競合した 400 は
		// saveChatHandler が接ぎ直しリカバリする。接ぎ直しが起きたら wire は
		// merged で組み直す(通知メッセージをモデルに載せる)。恒久失敗は送信中止
		let saveRes: { ok: boolean; history?: ChatHistory };
		try {
			if (createsChat) {
				try {
					// 一覧の取り直しは直後の saveChatHandler に任せる(送信 1 回で 1 回)
					_chatId = await session.initChatHandler(_history, { refreshList: false });
				} finally {
					clearLiveUsage(draftChatId, responseMessageId);
				}
				markLiveUsagePending(_chatId, responseMessageId);
			}

			await tick();

			_history = JSON.parse(JSON.stringify(session.history));
			saveRes = await session.saveChatHandler(_chatId, _history);
		} catch {
			// 400 以外の失敗(接続断・5xx・認証切れ)と chat 作成の失敗。送信対を残すと
			// 未完の応答が先端に居座り、以後の送信が「応答待ち」で弾かれて固まる —
			// 400 の後始末(saveChatHandler)と同じく送信前の形へ巻き戻す
			if (session.history.currentId === responseMessageId) {
				session.history = stripLocalTurn(session.history);
			}
			saveRes = { ok: false };
		}
		if (!saveRes.ok) {
			clearLiveUsage(_chatId, responseMessageId);
			// chat 作成直後の保存失敗では、作成済みの chat(user 発話だけ)がサーバーに残る。
			// $chatId は戻さない — '' に戻すと Chat.svelte の購読が initNewChat を走らせ、
			// ?q= 付きなら自動再送の繰り返しになる。手元は空に戻してあるので、再送は
			// root からの送信として新しい chat を作り直す(残った chat には足さない)
			toast.error(
				newChat
					? this.#t('Failed to save the chat. Your message was restored to the input.')
					: this.#t('Failed to save the chat.')
			);
			return false;
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

		// サイドバー一覧は直前の saveChatHandler が取り直し済み(ここで重ねて取らない)
		return true;
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

		// file.id 基準で合流(メタデータ違いの同一 file を 2 件送らない — chat-files.ts)
		const files: ChatFile[] = mergeRequestFiles(session.chatFiles, userMessage?.files);

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
				(session.selectedModels[0] === model.id || session.atSelectedModel !== undefined) &&
				this.#hasDefaultTitle()
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
			clearLiveUsage(_chatId, responseMessageId);
			if (abortController.signal.aborted) {
				// stop によるユーザー中断 — エラーにしない(task-cancelled と同じ扱い)。
				// ターン末の保存へは下の !res 分岐から interrupted 付きで流れる
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
				// 消費中の接続断(モバイルのタブ背面化で fetch が殺される・回線の瞬断等)。
				// 応答の失敗ではない — タスクはサーバー側で走り続け、最終形はサーバーが
				// 保存する。message.error には書かない(書くと再アタッチ後の done で
				// completed → 保存に乗って永続化される)。ここの responseMessage は送信時の
				// コピーなので、ライブの message へ書き戻しもしない(受信済みの本文を消す)。
				clearLiveUsage(_chatId, responseMessageId);
				if (session.history.messages[responseMessageId]?.done === true) {
					// done 受信後(chat:title 待ち等)の切断 — ターンは確定済み
					console.warn('completion stream disconnected after done', error);
					return;
				}
				// done 前の切断 — 再アタッチ / 読み直しで追いつく(Chat.svelte)
				console.warn('completion stream disconnected before done', error);
				this.#onStreamLost(_chatId, responseMessageId);
			});
		} else {
			// llens: task 生成前の失敗(接続エラー等)は done イベントが来ないため、
			// ここからターン末の処理(error 付き message の保存)を通す
			clearLiveUsage(_chatId, responseMessageId);
			await this.#events.chatCompletedHandler(_chatId);
		}

		await tick();
		this.#scrollToBottom();
	};

	// 最初の発話のターン(新規・最初の応答の再生成・最初の発話の編集)でタイトルを
	// 生成してよいか。付いているタイトルが既定(作成時の「New Chat」)のときだけ —
	// 利用者が手で付けたタイトルを再生成・編集で上書きしない。手動か自動生成かは
	// 区別できないため、生成済みのタイトルも保持する。session.chat は直前の保存
	// (saveChatHandler)の応答なのでサーバの現在値を指す
	#hasDefaultTitle = (): boolean => {
		const title = this.#session.chat?.title;
		return !title || title === 'New Chat' || title === this.#t('New Chat');
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
		if (this.#stopping) {
			return;
		}
		this.#stopping = true;
		try {
			await this.#stopResponse();
		} finally {
			this.#stopping = false;
		}
	};

	#stopResponse = async () => {
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
					// 404(Task not found)は止めに行った時点で task が既に終わっていた —
					// 停止の目的は果たされているのでエラーにしない(完走直後の押下・
					// 終端待ちの間の再押下)
					if (!/not found/i.test(`${error}`)) {
						toast.error(`${error}`);
					}
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
