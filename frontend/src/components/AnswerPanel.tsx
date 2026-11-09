import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, jsonBody } from "../api";

type Citation = {
  sourceId: string;
  documentId: string;
  documentTitle: string;
  pageNumber: number | null;
  score: number;
};

type AnswerResponse = {
  success: true;
  status: "answered" | "insufficient_evidence";
  answer: string;
  citations: Citation[];
  /** New answer API protocol marker: missing means an older backend is running. */
  engineVersion?: string;
  /** Present only with explicitly enabled local development diagnostics. */
  diagnostic?: string;
  verifiedSourceCount?: number;
};

type Props = {
  knowledgeBaseId: string;
  knowledgeBaseName: string;
};

// Versioned contract shared with the backend's authenticated read-only health
// endpoint. Do not let a legacy Node process silently serve old QA behavior.
const EXPECTED_ANSWER_ENGINE_VERSION = "phase3.10-grounded-2026-11-10-r4";

/**
 * User-facing Phase 3 answer experience. Uses the same authenticated, scoped
 * /answer endpoint as the diagnostic scripts. No alternate route to Qdrant or
 * Ollama, and no private source excerpts are stored in browser state.
 */
export default function AnswerPanel({ knowledgeBaseId, knowledgeBaseName }: Props) {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<AnswerResponse | null>(null);
  const [asked, setAsked] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [engineStatus, setEngineStatus] = useState<"checking" | "ready" | "outdated">("checking");
  const active = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    api<{ success: true; engineVersion: string }>("/answer-engine", { signal: controller.signal })
      .then(info => {
        if (!controller.signal.aborted) {
          setEngineStatus(info.engineVersion === EXPECTED_ANSWER_ENGINE_VERSION ? "ready" : "outdated");
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) setEngineStatus("outdated");
      });
    return () => controller.abort();
  }, []);

  useEffect(() => () => {
    active.current?.abort();
    active.current = null;
  }, []);

  async function submitQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = question.trim();
    if (active.current || engineStatus !== "ready" || text.length < 2 || text.length > 1000) return;

    const controller = new AbortController();
    active.current = controller;
    setPending(true);
    setError("");
    setResult(null);
    setAsked(text);

    try {
      const data = await api<AnswerResponse>(
        `/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/answer`,
        {
          method: "POST",
          ...jsonBody({ question: text, topK: 5 }),
          signal: controller.signal,
        },
      );
      if (controller.signal.aborted) return;
      if ((data.status !== "answered" && data.status !== "insufficient_evidence") ||
          typeof data.answer !== "string" || !Array.isArray(data.citations) ||
          (data.status === "answered" && (
            !data.answer.trim() || data.citations.length === 0 ||
            !data.citations.every(citation =>
              typeof citation.sourceId === "string" &&
              data.answer.includes(`[${citation.sourceId}]`))
          )) ||
          (data.status === "insufficient_evidence" && data.citations.length !== 0)) {
        throw new Error("Answer service returned an unverified response.");
      }
      setResult(data);
    } catch (cause) {
      if (!controller.signal.aborted) {
        setError(cause instanceof Error ? cause.message : "Could not get an answer.");
      }
    } finally {
      if (active.current === controller) {
        active.current = null;
        setPending(false);
      }
    }
  }

  function reset() {
    if (active.current) {
      active.current.abort();
      active.current = null;
    }
    setPending(false);
    setQuestion("");
    setAsked("");
    setResult(null);
    setError("");
  }

  return (
    <section className="answer-panel" aria-labelledby="answer-panel-heading">
      <div className="answer-head">
        <div>
          <p className="eyebrow">DOCUMENT-GROUNDED ANSWERS</p>
          <h2 id="answer-panel-heading">Ask your knowledge base</h2>
          <p className="subtle">
            Answers use only verified, accessible documents in <strong>{knowledgeBaseName}</strong>.
            English, বাংলা and mixed-language questions are supported.
          </p>
        </div>
        <span className="answer-readonly">Read only</span>
      </div>
      <div role="status" className={engineStatus === "outdated" ? "answer-engine-indicator warning" : "answer-engine-indicator"}>
        {engineStatus === "checking" && "Checking answer backend version…"}
        {engineStatus === "ready" && "Answer backend connected · current engine verified"}
        {engineStatus === "outdated" && (
          "The running backend is outdated or unreachable. Stop the old port 4000 process and restart the updated backend."
        )}
      </div>

      <form className="answer-form" onSubmit={submitQuestion}>
        <label htmlFor="answer-question">Your question</label>
        <textarea
          id="answer-question"
          rows={3}
          maxLength={1000}
          minLength={2}
          required
          value={question}
          onChange={event => setQuestion(event.target.value)}
          placeholder="Ask about a fact in your documents… / ডকুমেন্ট থেকে প্রশ্ন করুন…"
          disabled={pending}
        />
        <div className="answer-form-footer">
          <span className="answer-counter">{question.length}/1000 characters</span>
          <div className="answer-controls">
            <button className="secondary" type="button" onClick={reset}>
              Clear
            </button>
            <button className="primary" type="submit" disabled={pending || engineStatus !== "ready" || question.trim().length < 2}>
              {pending ? "Finding evidence…" : "Ask DocMind-AI"}
            </button>
          </div>
        </div>
      </form>

      <div className="answer-output" aria-live="polite" aria-atomic="true">
        {pending && <p className="answer-pending" role="status">Searching authorized documents and checking evidence…</p>}
        {error && (
          <div className="message error" role="alert">
            <strong>Answer unavailable.</strong> {error}
            <p className="answer-help">Check that the backend and local model services are running. Your documents were not modified.</p>
          </div>
        )}
        {result && (
          <div className="answer-result">
            <div className="answer-result-head">
              <span className={result.status === "answered" ? "answer-state answered" : "answer-state abstained"}>
                {result.status === "answered" ? "Evidence-backed answer" : "Insufficient evidence"}
              </span>
              <span className="answer-readonly">Knowledge base: {knowledgeBaseName}</span>
            </div>
            <p className="answer-asked"><strong>Question:</strong> {asked}</p>
            <p className="answer-response">{result.answer}</p>
            {result.citations.length > 0 && (
              <div className="answer-citations">
                <h3>Document sources</h3>
                <ul>
                  {result.citations.map(citation => (
                    <li key={citation.sourceId + ":" + citation.documentId}>
                      <strong>[{citation.sourceId}]</strong> {citation.documentTitle}
                      {citation.pageNumber !== null && <span> · Page {citation.pageNumber}</span>}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <div className="answer-runtime">
              <span>
                {result.engineVersion
                  ? `Answer engine: ${result.engineVersion}`
                  : "Backend version missing — an older server may still be running"}
              </span>
              {result.diagnostic && <span>Resolution: {result.diagnostic}</span>}
              {typeof result.verifiedSourceCount === "number" && (
                <span>Verified passages considered: {result.verifiedSourceCount}</span>
              )}
            </div>
          </div>
        )}
        {!pending && !error && !result && (
          <p className="answer-placeholder">
            No question asked yet. Select a knowledge base and ask about one of its uploaded documents.
          </p>
        )}
      </div>
    </section>
  );
}
