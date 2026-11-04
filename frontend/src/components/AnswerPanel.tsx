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
};

type Props = {
  knowledgeBaseId: string;
  knowledgeBaseName: string;
};

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
  const active = useRef<AbortController | null>(null);

  useEffect(() => () => {
    active.current?.abort();
    active.current = null;
  }, []);

  async function submitQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = question.trim();
    if (active.current || text.length < 2 || text.length > 1000) return;

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
            <button className="primary" type="submit" disabled={pending || question.trim().length < 2}>
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
              <span className="answer-readonly">Knowledge base scoped</span>
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
