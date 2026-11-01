/**
 * DocMind-AI Phase 3.5: read-only, controlled answer-quality baseline.
 * Paste this entire file into Chrome DevTools Console while logged into the QA workspace.
 * Runs nine /answer requests against ONLY the isolated three-document QA KB.
 * No uploads, indexing, data mutation, credentials, private excerpts, or raw answers are logged.
 * Fixed 17-second spacing respects the current four-answer-requests/minute limiter
 * when no other caller is competing; stop immediately on HTTP 429.
 * A PASS here is a smoke-test result, NOT a general factual-grounding guarantee.
 */
void (async () => {
  "use strict";
  const QA_KB_ID = "cmvxmqrea0000c8l1pcvmh7oa";
  const ENDPOINT = `http://localhost:4000/api/v1/knowledge-bases/${QA_KB_ID}/answer`;
  const REQUEST_SPACING_MS = 17_000;
  const titles = {
    vectors: "phase3_3d_vectors.txt",
    security: "phase3_3d_security.txt",
    storage: "phase3_3d_storage.txt",
  };
  const cases = [
    { id: "en-vectors", language: "english", question: "Which vector database stores DocMind-AI embeddings?", title: titles.vectors, fact: /\bQdrant\b/iu },
    { id: "en-security", language: "english", question: "What HTTP status code is returned when accessing an inaccessible knowledge base?", title: titles.security, fact: /\b404\b|৪০৪/u },
    { id: "en-negative", language: "english", question: "What is the favorite food of the founder of Atlantis?", title: null },
    { id: "bn-vectors", language: "bangla", question: "ডকুমেন্ট এমবেডিং কোন ভেক্টর ডাটাবেসে রাখা হয়?", title: titles.vectors, fact: /\bQdrant\b|কিউড্রান্ট/iu },
    { id: "bn-storage", language: "bangla", question: "আপলোড করা মূল ফাইলগুলো কোন স্টোরেজে রাখা হয়?", title: titles.storage, fact: /\bRustFS\b|রাস্টএফএস/iu },
    { id: "bn-negative", language: "bangla", question: "চাঁদের রাজপ্রাসাদের ফোন নম্বর কত?", title: null },
    { id: "mixed-security", language: "mixed", question: "Cross-org KB access কোন HTTP status return করে?", title: titles.security, fact: /\b404\b|৪০৪/u },
    { id: "mixed-storage", language: "mixed", question: "DocMind-AI source documents কোন object store-এ থাকে?", title: titles.storage, fact: /\bRustFS\b|রাস্টএফএস/iu },
    { id: "mixed-negative", language: "mixed", question: "DocMind-AI এর Mars headquarters address কী?", title: null },
  ];
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const rows = [];
  const finish = (complete) => {
    console.table(rows);
    const passed = rows.filter(row => row.pass).length;
    console.log(`Phase 3.5 QA: ${passed}/${rows.length} passed; ${cases.length} planned; complete=${complete}.`);
    console.log("This is a small, controlled answer-quality baseline, not proof of general reliability.");
  };
  for (let i = 0; i < cases.length; i++) {
    const item = cases[i];
    if (i !== 0) await sleep(REQUEST_SPACING_MS);
    let response;
    try {
      response = await fetch(ENDPOINT, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json", "X-DocMind-Request": "1" },
        body: JSON.stringify({ question: item.question, topK: 3 }),
      });
    } catch {
      console.error(`Stopped at ${item.id}: network error. Nothing was modified.`);
      finish(false); return;
    }
    if (response.status === 429) {
      console.warn(`Stopped at ${item.id}: HTTP 429 rate limit. Allow the window to reset before rerunning.`);
      finish(false); return;
    }
    if (response.status !== 200) {
      console.error(`Stopped at ${item.id}: HTTP ${response.status}. Check authorization/backend health.`);
      finish(false); return;
    }
    let data;
    try { data = await response.json(); }
    catch { console.error(`Stopped at ${item.id}: invalid JSON response.`); finish(false); return; }
    const citations = Array.isArray(data.citations) ? data.citations : [];
    const answer = typeof data.answer === "string" ? data.answer : "";
    const answerFactMatch = item.title === null ? null : item.fact.test(answer);
    const citationCorrect = item.title === null
      ? citations.length === 0 && !/\[S\d+\]/u.test(answer)
      : citations.length === 1 && citations[0]?.documentTitle === item.title &&
        typeof citations[0]?.sourceId === "string" &&
        answer.includes(`[${citations[0].sourceId}]`);
    const pass = item.title === null
      ? data.status === "insufficient_evidence" && citationCorrect
      : data.status === "answered" && answerFactMatch && citationCorrect;
    rows.push({
      case_id: item.id,
      language: item.language,
      http: response.status,
      status: String(data.status ?? "missing"),
      fact_match: answerFactMatch,
      correct_citation: citationCorrect,
      citations: citations.length,
      pass: Boolean(pass),
    });
    console.log(`${item.id}: ${pass ? "PASS" : "FAIL"}`);
  }
  finish(true);
})().catch(() => console.error("Phase 3.5 evaluation stopped unexpectedly; no data was modified."));