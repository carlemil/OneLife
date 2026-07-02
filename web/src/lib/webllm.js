// In-browser LLM client (WebGPU via @mlc-ai/web-llm) — the "browser" provider.
//
// The server builds every prompt (llm.py build_*), ships it as a pending_inference
// envelope, and we execute it here on the player's own GPU, posting the raw
// completions back for the server to parse + apply. The heavy library is loaded
// lazily (dynamic import) so deployments on the anthropic/stub providers never pay
// for it.
//
// Trust note: verdicts produced here are client-run and thus client-trusted. The
// server still clamps/filters them; the blast radius is a player cheating their own
// save. The decision-mirroring below (successRuleMet) MUST stay in lockstep with
// gates.py `_turn_satisfied` / llm.success_rule_met.

let enginePromise = null; // in-flight or resolved engine (load once)
let _ready = false;
let _model = null;

export function isReady() { return _ready; }
export function currentModel() { return _model; }

// Kick off model load. Safe to call repeatedly — only the first call loads. Rejects
// with err.noWebGPU=true when the browser can't run WebGPU at all.
export function initEngine(model, onProgress) {
  if (enginePromise) return enginePromise;
  if (typeof navigator === 'undefined' || !navigator.gpu) {
    const e = new Error('WebGPU is not available in this browser.');
    e.noWebGPU = true;
    return Promise.reject(e);
  }
  _model = model;
  enginePromise = (async () => {
    const { CreateWebWorkerMLCEngine } = await import('@mlc-ai/web-llm');
    const worker = new Worker(new URL('./webllm.worker.js', import.meta.url),
                              { type: 'module' });
    const engine = await CreateWebWorkerMLCEngine(worker, model, {
      initProgressCallback: (p) => { onProgress && onProgress(p); },
    });
    _ready = true;
    return engine;
  })();
  enginePromise.catch(() => { enginePromise = null; }); // allow retry on failure
  return enginePromise;
}

async function _engine() {
  if (!enginePromise) throw new Error('WebLLM engine has not been started');
  return enginePromise; // resolves once weights finish loading
}

// Run one InferenceRequest (server-built) and return the raw completion string.
async function runRequest(req) {
  const engine = await _engine();
  const messages = [{ role: 'system', content: req.system }, ...(req.messages || [])];
  const opts = { messages, max_tokens: req.max_tokens, stream: false };
  // WebLLM does grammar-constrained JSON decoding via response_format with a
  // stringified schema — this is what makes small models emit schema-valid JSON.
  const js = req.response_format && req.response_format.json_schema;
  if (js) opts.response_format = { type: 'json_object', schema: JSON.stringify(js.schema) };
  const res = await engine.chat.completions.create(opts);
  return res.choices?.[0]?.message?.content ?? '';
}

// --- decision mirror (keep in lockstep with the server) --------------------- //
function successRuleMet(rule, met) {
  const set = new Set(met);
  if (!rule) return false;
  if (rule.includes(' AND ')) return rule.split(' AND ').every((t) => set.has(t.trim()));
  if (rule.includes(' OR ')) return rule.split(' OR ').some((t) => set.has(t.trim()));
  return set.has(rule.trim());
}

function parseMet(raw, decision) {
  let data = {};
  try { data = JSON.parse(raw); } catch { /* junk → no criteria */ }
  const valid = new Set(decision.criteria);
  const met = (Array.isArray(data.criteria_met) ? data.criteria_met : [])
    .filter((m) => valid.has(m));
  return Array.from(new Set([...met, ...decision.already_met])).sort();
}

function decideSatisfied(decision, met) {
  if (successRuleMet(decision.success_rule, met)) return true;
  const mercy = decision.mercy_after_attempts;
  return mercy != null && decision.attempts >= mercy && met.length >= 1;
}

// Execute a whole pending_inference. For a gate turn (decision present) we run the
// alignment + referee, mirror the pass decision, and run exactly ONE Actor variant
// so we never generate the paragraph we won't use. Everything else just runs each
// request. Returns { completions: {id: rawText} } keyed by request id.
export async function runInference(pending) {
  const byId = {};
  for (const r of pending.requests || []) byId[r.id] = r;
  const completions = {};
  const d = pending.decision;

  if (d) {
    if (byId.align) completions.align = await runRequest(byId.align);
    if (d.already_satisfied) {
      if (byId.actor_helped) completions.actor_helped = await runRequest(byId.actor_helped);
    } else {
      if (byId.referee) completions.referee = await runRequest(byId.referee);
      const met = parseMet(completions.referee || '', d);
      const key = decideSatisfied(d, met) ? 'actor_reveal' : 'actor';
      if (byId[key]) completions[key] = await runRequest(byId[key]);
    }
  } else {
    for (const r of pending.requests || []) completions[r.id] = await runRequest(r);
  }
  return completions;
}
