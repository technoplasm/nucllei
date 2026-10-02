/**
 * MessageInput の添付ハンドラ群(#70 C トラック: MessageInput.svelte 分割)。
 *
 * ファイル/画像のアップロード・ライブラリ再添付・添付解除・入力振り分けを持つ。
 * `files` は親($bindable prop)が所有するため、アクセサ(getFiles / setFiles)経由で
 * 読み書きする — $state プロキシに乗せた後のエントリ更新は closure の生オブジェクトではなく
 * 必ず `files[idx] = {...files[idx], ...}` とプロキシ経由で置き換える(dev-environment.md 罠 (6))。
 */
import { get } from 'svelte/store';
import { toast } from 'svelte-sonner';
import { v4 as uuidv4 } from 'uuid';

import { config, user, type Model } from '$lib/stores';
import { blobToFile, compressImage } from '$lib/utils';
import { uploadFile, deleteFileById, processFileById } from '$lib/apis/files';
import { WEBUI_API_BASE_URL } from '$lib/constants';

import { type Translate } from '../Chat/session.svelte';

export class MessageInputUploads {
	#t: Translate;
	#getFiles: () => any[];
	#setFiles: (files: any[]) => void;
	#getVisionCapableModels: () => (string | Model)[];

	constructor({
		t,
		getFiles,
		setFiles,
		getVisionCapableModels
	}: {
		t: Translate;
		getFiles: () => any[];
		setFiles: (files: any[]) => void;
		getVisionCapableModels: () => (string | Model)[];
	}) {
		this.#t = t;
		this.#getFiles = getFiles;
		this.#setFiles = setFiles;
		this.#getVisionCapableModels = getVisionCapableModels;
	}

	uploadFileHandler = async (file: File, fullContext: boolean = false) => {
		const currentUser = get(user);
		if (currentUser?.role !== 'admin' && !(currentUser?.permissions?.chat?.file_upload ?? true)) {
			toast.error(this.#t('You do not have permission to upload files.'));
			return null;
		}

		if (file.size == 0) {
			toast.error(this.#t('You cannot upload an empty file.'));
			return null;
		}

		await this.#uploadFileItem(file, { fullContext }).catch((e) => {
			toast.error(`${e}`);
		});
	};

	// ファイルをアップロードして files に添付チップを足す。チップの itemId を返す(アップロード中に
	// X で外された等で残らなかったら null)。失敗したらチップを外して throw する
	#uploadFileItem = async (
		file: File,
		{
			fullContext = false,
			dictation
		}: { fullContext?: boolean; dictation?: Record<string, unknown> } = {}
	): Promise<string | null> => {
		const tempItemId = uuidv4();
		const fileItem = {
			type: 'file',
			file: '',
			id: null,
			url: '',
			name: file.name,
			collection_name: '',
			status: 'uploading',
			size: file.size,
			error: '',
			itemId: tempItemId,
			...(fullContext ? { context: 'full' } : {})
		};

		this.#setFiles([...this.#getFiles(), fileItem]);

		try {
			// During the file upload, file content is automatically extracted.
			// llens(#69): グローバル Attachment(ATTACHMENT_FUNCTION_ID)はサーバー側で発火する。
			const uploadedFile = await uploadFile(file, dictation);

			if (!uploadedFile) {
				this.#setFiles(this.#getFiles().filter((item) => item?.itemId !== tempItemId));
				return null;
			}

			// アップロード中に X で外された場合はサーバーに orphan を残さない
			const files = this.#getFiles();
			const idx = files.findIndex((item) => item?.itemId === tempItemId);
			if (idx === -1) {
				deleteFileById(uploadedFile.id).catch(() => {});
				return null;
			}

			if (uploadedFile.error) {
				console.warn('File upload warning:', uploadedFile.error);
				toast.warning(uploadedFile.error);
			}

			// 通し録音の区間一覧(meta.dictation)は本文と同じ量の文字を持つ。チャットに残る
			// スナップショットには載せない(読むのは配備側の tool で、Files から引く)
			const { dictation: _dictation, ...meta } = uploadedFile.meta ?? {};

			// runes の $state プロキシに乗せた後の closure(生オブジェクト)を書いても
			// 再描画されない — 必ず files[idx] 経由で置き換える(status が 'uploading' に
			// 張り付くバグの実害箇所)
			files[idx] = {
				...files[idx],
				status: 'uploaded',
				file: { ...uploadedFile, meta },
				id: uploadedFile.id,
				collection_name: uploadedFile?.meta?.collection_name || uploadedFile?.collection_name,
				url: `${WEBUI_API_BASE_URL}/files/${uploadedFile.id}`
			};
			return tempItemId;
		} catch (e) {
			this.#setFiles(this.#getFiles().filter((item) => item?.itemId !== tempItemId));
			throw e;
		}
	};

	// 音声入力の通し録音を、この発言の添付に残せるか(VoiceRecording が録音開始時に聞く)。
	// 権限が無い利用者・その拡張子を受けない配備では、黙って残さない(音声入力は今までどおり)
	canKeepRecording = (extension: string) => {
		const currentUser = get(user);
		if (currentUser?.role !== 'admin' && !(currentUser?.permissions?.chat?.file_upload ?? true)) {
			return false;
		}
		const allowedExtensions = get(config)?.file?.allowed_extensions ?? [];
		return allowedExtensions.length === 0 || allowedExtensions.includes(extension);
	};

	// 音声入力の通し録音(長い録音は複数本)を添付にする。全部残せたら true。失敗の知らせは
	// 呼び出し側が 1 回だけ出す(ここではトーストを出さない)。待っている間に録音が × で
	// 取り消されたら、足した分をすべて外してサーバーからも消す(× では何も残さない)
	keepRecordingHandler = async (
		recordings: { file: File; dictation: Record<string, unknown> }[],
		isCancelled: () => boolean
	) => {
		const itemIds: string[] = [];
		let kept = true;

		for (const { file, dictation } of recordings) {
			const itemId = await this.#uploadFileItem(file, { dictation }).catch((e) => {
				console.error('Keeping the voice recording failed:', e);
				return null;
			});
			if (itemId) {
				itemIds.push(itemId);
			} else {
				kept = false;
			}
			if (isCancelled()) break;
		}

		if (isCancelled()) {
			const files = this.#getFiles();
			for (const item of files.filter((item) => itemIds.includes(item?.itemId))) {
				deleteFileById(item.id).catch(() => {});
			}
			this.#setFiles(files.filter((item) => !itemIds.includes(item?.itemId)));
			return false;
		}

		return kept;
	};

	// llens(#69): 画像も file として backend に保存する(base64 直入れを廃止)。
	// chat JSON には正準形 "/api/v1/files/{id}/content" の相対 URL を保存する
	// (送信 payload 中のこの形式だけ backend が base64 化する)。
	// アップロード中はローカル objectURL でプレビュー(status:'uploading')。
	uploadImageHandler = async (file: File) => {
		const currentUser = get(user);
		if (currentUser?.role !== 'admin' && !(currentUser?.permissions?.chat?.file_upload ?? true)) {
			toast.error(this.#t('You do not have permission to upload files.'));
			return null;
		}

		if (file.size == 0) {
			toast.error(this.#t('You cannot upload an empty file.'));
			return null;
		}

		const tempItemId = uuidv4();
		const previewUrl = URL.createObjectURL(file);
		const fileItem = {
			type: 'image',
			file: '',
			id: null,
			url: previewUrl,
			name: file.name,
			status: 'uploading',
			size: file.size,
			error: '',
			itemId: tempItemId
		};

		this.#setFiles([...this.#getFiles(), fileItem]);

		try {
			// llens: 画像縮小は配備設定(ATTACHMENT_IMAGE_MAX_DIMENSION → config.file.
			// image_max_dimension)。長辺が上限を超える画像だけ dataURL 経由で縮小
			// (MIME 維持)し、file に戻してからアップロード
			let uploadTarget = file;

			const maxDimension = get(config)?.file?.image_max_dimension ?? null;
			if (maxDimension) {
				const dataUrl = await new Promise<string>((resolve, reject) => {
					const reader = new FileReader();
					// readAsDataURL の onload では result は常に string
					reader.onload = () => resolve(reader.result as string);
					reader.onerror = reject;
					reader.readAsDataURL(file);
				});

				const compressedUrl = await compressImage(dataUrl, maxDimension, file.type);
				if (compressedUrl !== dataUrl) {
					const compressedBlob = await (await fetch(compressedUrl)).blob();
					uploadTarget = blobToFile(compressedBlob, file.name);
				}
			}

			const uploadedFile = await uploadFile(uploadTarget);

			if (uploadedFile) {
				// アップロード中に X で外された場合はサーバーに orphan を残さない
				const files = this.#getFiles();
				const idx = files.findIndex((item) => item?.itemId === tempItemId);
				if (idx === -1) {
					deleteFileById(uploadedFile.id).catch(() => {});
					return null;
				}

				if (uploadedFile.error) {
					console.warn('File upload warning:', uploadedFile.error);
					toast.warning(uploadedFile.error);
				}

				// uploadFileHandler と同じ理由で files[idx] 経由で置き換える
				files[idx] = {
					...files[idx],
					status: 'uploaded',
					file: uploadedFile,
					id: uploadedFile.id,
					name: uploadedFile?.meta?.name ?? file.name,
					size: uploadedFile?.meta?.size ?? file.size,
					url: `/api/v1/files/${uploadedFile.id}/content`
				};
			} else {
				this.#setFiles(this.#getFiles().filter((item) => item?.itemId !== tempItemId));
			}
		} catch (e) {
			toast.error(`${e}`);
			this.#setFiles(this.#getFiles().filter((item) => item?.itemId !== tempItemId));
		} finally {
			URL.revokeObjectURL(previewUrl);
		}
	};

	// llens(#69): 保存済みファイルの再添付。processFileById で Attachment フックを再発火し
	// (発火は owner/admin のみサーバー側で制御)、戻りの FileModel から files エントリを組む。
	// origin:'library' — dismiss 時に実体削除しない(添付解除のみ)。
	libraryFilesHandler = async (selected: { id: string; meta?: { content_type?: string } }[]) => {
		for (const item of selected ?? []) {
			if (this.#getFiles().some((f) => f?.id === item.id)) {
				continue;
			}

			const isImage = (item?.meta?.content_type ?? '').startsWith('image/');
			if (isImage && this.#getVisionCapableModels().length === 0) {
				toast.error(this.#t('Selected model(s) do not support image inputs'));
				continue;
			}

			try {
				const file = (await processFileById(item.id)) ?? item;

				if (isImage) {
					this.#setFiles([
						...this.#getFiles(),
						{
							type: 'image',
							id: file.id,
							url: `/api/v1/files/${file.id}/content`,
							name: file?.meta?.name ?? file?.filename,
							size: file?.meta?.size,
							status: 'uploaded',
							itemId: uuidv4(),
							origin: 'library'
						}
					]);
				} else {
					this.#setFiles([
						...this.#getFiles(),
						{
							type: 'file',
							file: file,
							id: file.id,
							url: `${WEBUI_API_BASE_URL}/files/${file.id}`,
							name: file?.meta?.name ?? file?.filename,
							collection_name: file?.meta?.collection_name ?? file?.collection_name ?? '',
							status: 'uploaded',
							size: file?.meta?.size,
							error: '',
							itemId: uuidv4(),
							origin: 'library'
						}
					]);
				}
			} catch (e) {
				toast.error(`${e}`);
			}
		}
	};

	// llens(#69): dismiss の意味論 — 新規アップロード分は実体削除、
	// library 再添付分(origin:'library')は添付解除のみ。
	dismissFileHandler = async (fileIdx: number) => {
		const files = this.#getFiles();
		const file = files[fileIdx];

		if (file?.type !== 'collection' && !file?.collection && file?.origin !== 'library') {
			if (file?.id) {
				// #94: 削除は冪等な soft delete — 失敗してもチップは外す(残留させない)
				await deleteFileById(file.id).catch((err) => {
					console.log(err);
					return null;
				});
			}
		}

		// Remove from UI state
		files.splice(fileIdx, 1);
	};

	inputFilesHandler = async (inputFiles: File[]) => {
		inputFiles.forEach((file) => {
			if (
				(get(config)?.file?.max_size ?? null) !== null &&
				file.size > (get(config)?.file?.max_size ?? 0) * 1024 * 1024
			) {
				toast.error(
					this.#t(`File size should not exceed {{maxSize}} MB.`, {
						maxSize: get(config)?.file?.max_size
					})
				);
				return;
			}

			if (
				['image/gif', 'image/webp', 'image/jpeg', 'image/png', 'image/avif'].includes(file['type'])
			) {
				if (this.#getVisionCapableModels().length === 0) {
					toast.error(this.#t('Selected model(s) do not support image inputs'));
					return;
				}
				this.uploadImageHandler(file);
			} else {
				this.uploadFileHandler(file);
			}
		});
	};
}
