// Web Worker host for the WebLLM engine, so model inference runs off the main
// thread and never freezes the UI. The engine is created on the main thread via
// CreateWebWorkerMLCEngine(worker, ...) in webllm.js; this handler services it.
import { WebWorkerMLCEngineHandler } from '@mlc-ai/web-llm';

const handler = new WebWorkerMLCEngineHandler();
self.onmessage = (msg) => handler.onmessage(msg);
