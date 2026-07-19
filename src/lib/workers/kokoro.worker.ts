import { env } from '@huggingface/transformers';
import { KokoroTTS } from 'kokoro-js';

// TODO: Below doesn't work as expected, need to investigate further
if (env.backends.onnx.wasm) {
	env.backends.onnx.wasm.wasmPaths = '/wasm/';
}

let tts: KokoroTTS | null = null;
let isInitialized = false; // Flag to track initialization status
const DEFAULT_MODEL_ID = 'onnx-community/Kokoro-82M-v1.0-ONNX'; // Default model

self.onmessage = async (event) => {
	const { type, payload } = event.data;

	if (type === 'init') {
		const { dtype } = payload;
		let { model_id } = payload;
		model_id = model_id || DEFAULT_MODEL_ID; // Use default model if none provided

		self.postMessage({ status: 'init:start' });

		try {
			tts = await KokoroTTS.from_pretrained(model_id, {
				dtype,
				// WebGPU 検出(lib.dom 未収載の navigator.gpu を型だけ拡張)
				device: (navigator as Navigator & { gpu?: unknown })?.gpu ? 'webgpu' : 'wasm'
			});
			isInitialized = true; // Mark as initialized after successful loading
			self.postMessage({ status: 'init:complete' });
		} catch (error) {
			isInitialized = false; // Ensure it's marked as false on failure
			self.postMessage({
				status: 'init:error',
				error: error instanceof Error ? error.message : `${error}`
			});
		}
	}

	if (type === 'generate') {
		if (!isInitialized || !tts) {
			// Ensure model is initialized
			self.postMessage({ status: 'generate:error', error: 'TTS model not initialized' });
			return;
		}

		const { text, voice } = payload;
		self.postMessage({ status: 'generate:start' });

		try {
			const rawAudio = await tts.generate(text, { voice });
			const blob = await rawAudio.toBlob();
			const blobUrl = URL.createObjectURL(blob);
			self.postMessage({ status: 'generate:complete', audioUrl: blobUrl });
		} catch (error) {
			self.postMessage({
				status: 'generate:error',
				error: error instanceof Error ? error.message : `${error}`
			});
		}
	}

	if (type === 'status') {
		// Respond with the current initialization status
		self.postMessage({ status: 'status:check', initialized: isInitialized });
	}
};
