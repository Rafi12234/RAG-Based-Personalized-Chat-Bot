/**
 * DocMind-AI Phase 3.8 — ONE-SHOT 10-case read-only diagnostic.
 * Open logged-in http://localhost:5173 -> F12 -> Console, paste entire file.
 *
 * Only requests GET document/health status and POST retrieval/answer.
 * Does not upload, modify, index, archive or delete anything.
 * Fixed to the disposable synthetic Phase 3.8 KB; not the Phase 2 KB.
 *
 * Before running, restart the Node backend with:
 *   set NODE_ENV=development
 *   set DOCMIND_ANSWER_DIAGNOSTICS=1
 *   npm run dev
 * Then stop other benchmark scripts; answer API limit is 4/min.
 *
 * Does not log source excerpts, document IDs, cookies, credentials or prompts.
 * This evaluates a small synthetic fixture, NOT general factual entailment.
 */
void (async () => {
  "use strict";
  const BASE = "http://localhost:4000/api/v1";
  const KB_ID = "cmvz6m3h10000qol1hoahhd6r";
  const TOP_K = 5;
  const PAUSE_MS = 17_000;

  const POLICY = "phase3_8_dispatch_policy.txt";
  const COUNTER = "phase3_8_dispatch_counterstatement.txt";
  const SUPPORT = "phase3_8_support_handbook.txt";
  const ALL_FILES = [POLICY, COUNTER, SUPPORT];
  const has = (sources, file, pattern) =>
    sources.some(s => s.documentTitle === file &&
      pattern.test(typeof s.excerpt === "string" ? s.excerpt : ""));

  const cases = [
    {
      id: "east-cutoff",
      question: "What is the Route East same-day dispatch cutoff?",
      expected: "answer", document: POLICY,
      answerPattern: /14:30\s+Asia\/Dhaka/iu,
      evidence: s => has(s, POLICY, /Route East same-day dispatch cutoff is 14:30 Asia\/Dhaka/iu),
    },
    {
      id: "north-conflict",
      question: "What is the Route North same-day dispatch cutoff?",
      expected: "abstain",
      evidence: s =>
        has(s, POLICY, /Route North same-day dispatch cutoff is 16:00/iu) &&
        has(s, COUNTER, /Route North same-day dispatch cutoff is 15:00/iu),
    },
    {
      id: "damage-window",
      question: "How many hours after damage is identified must staff upload a photograph?",
      expected: "answer", document: POLICY,
      answerPattern: /\b72\s+hours?\b/iu,
      evidence: s => has(s, POLICY, /upload a photograph[^.!?]{0,100}within 72 hours/iu),
    },
    {
      id: "refund-approval",
      question: "When does the five-business-day refund processing period start?",
      expected: "answer", document: POLICY,
      answerPattern: /\bafter\b[^.!?]{0,90}\bapprov(?:al|ed)\b/iu,
      evidence: s => has(s, POLICY, /refund is processed within five business days after the refund request is approved/iu),
    },
    {
      id: "support-ack",
      question: "How soon should Cedarstone acknowledge a new ticket?",
      expected: "answer", document: SUPPORT,
      answerPattern: /\b(?:one|1)\s+business\s+day\b/iu,
      evidence: s => has(s, SUPPORT, /acknowledge a new ticket within one business day/iu),
    },
    {
      id: "support-phone",
      question: "What is Cedarstone's public support telephone number?",
      expected: "abstain",
      evidence: s => has(s, SUPPORT, /does not publish a public support phone number/iu),
    },
    {
      id: "attachments-retention",
      question: "How long are resolved ticket attachments retained?",
      expected: "answer", document: SUPPORT,
      answerPattern: /\b30\s+days?\b/iu,
      evidence: s => has(s, SUPPORT, /resolved ticket attachments are removed after 30 days/iu),
    },
    {
      id: "ticket-metadata-retention",
      question: "How long is resolved ticket metadata retained?",
      expected: "answer", document: SUPPORT,
      answerPattern: /\b90\s+days?\b/iu,
      evidence: s => has(s, SUPPORT, /metadata about the ticket is retained for 90 days/iu),
    },
    {
      id: "invoice-correction",
      question: "How many business days after receiving the invoice can a customer request a correction?",
      expected: "answer", document: SUPPORT,
      answerPattern: /\b(?:seven|7)\s+business\s+days?\b/iu,
      evidence: s => has(s, SUPPORT, /invoice correction within seven business days of receiving the invoice/iu),
    },
    {
      id: "support-login-secret",
      question: "What is the administrator password for Cedarstone Helpdesk?",
      expected: "abstain",
      evidence: s => has(s, SUPPORT, /does not specify encryption keys, database root credentials, or the phone number of the administrator/iu),
    },
  ];

  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  const call = async (path, question, timeout) => {
    const response = await fetch(BASE + path, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json", "X-DocMind-Request": "1" },
      body: JSON.stringify({ question, topK: TOP_K }),
      signal: AbortSignal.timeout(timeout),
    });
    let body = {};
    try { body = await response.json(); }
    catch { body = { message: "invalid_response_json" }; }
    return {
      http: response.status,
      body,
      code: response.headers.get("X-DocMind-Diagnostic-Code") || "",
    };
  };

  // Preflight checks that this is the isolated synthetic KB and all three
  // fixture files are READY. This never queries a different KB.
  let documents;
  try {
    const response = await fetch(BASE + "/knowledge-bases/" + KB_ID + "/documents", {
      credentials: "include",
      signal: AbortSignal.timeout(20_000),
    });
    if (response.status !== 200) {
      console.error("Preflight document-list HTTP " + response.status + ". Sign in to DocMind-AI first.");
      return;
    }
    const data = await response.json();
    documents = data.documents;
  } catch {
    console.error("Preflight failed: backend or session unavailable.");
    return;
  }
  if (!Array.isArray(documents) || documents.length !== 3 ||
      !ALL_FILES.every(file => documents.some(d =>
        d.title === file && d.latestVersion?.status === "READY"))) {
    console.error("Preflight stopped: expected exactly the three READY synthetic Phase 3.8 files.");
    console.table((Array.isArray(documents) ? documents : []).map(d => ({
      file: d.title, status: d.latestVersion?.status || "unknown",
    })));
    return;
  }

  let dbHealth = "unavailable";
  try {
    const response = await fetch(BASE + "/health/database", {
      signal: AbortSignal.timeout(10_000),
    });
    dbHealth = "HTTP " + response.status;
  } catch { dbHealth = "network_error"; }
  console.log("Phase 3.8: 10-case single-run diagnostic starting. Initial DB health:", dbHealth);
  console.log("Only safe response labels and synthetic file titles are logged; no source excerpts.");

  const rows = [];
  const count = cases.length;
  for (const [index, item] of cases.entries()) {
    if (index) await pause(PAUSE_MS);
    console.log("Checking " + (index + 1) + "/" + count + ": " + item.id);
    let retrieved;
    try {
      retrieved = await call("/knowledge-bases/" + KB_ID + "/retrieve", item.question, 45_000);
    } catch {
      rows.push({ case_id: item.id, retrieve_http: "network", sources: 0,
        evidence: false, answer_http: "skipped", status: "-", fact: "-",
        citation: "-", diagnostic: "retrieval_network_failure", result: "BLOCKED" });
      continue;
    }

    const sources = Array.isArray(retrieved.body.sources) ? retrieved.body.sources : [];
    const evidence = retrieved.http === 200 && item.evidence(sources);
    if (retrieved.http !== 200) {
      rows.push({ case_id: item.id, retrieve_http: retrieved.http, sources: 0,
        evidence: false, answer_http: "skipped", status: "-", fact: "-",
        citation: "-", diagnostic: retrieved.body.message || retrieved.code || "retrieval_error",
        result: "BLOCKED" });
      continue;
    }

    let response;
    try {
      response = await call("/knowledge-bases/" + KB_ID + "/answer", item.question, 145_000);
    } catch {
      rows.push({ case_id: item.id, retrieve_http: 200, sources: sources.length,
        evidence, answer_http: "network", status: "-", fact: "-",
        citation: "-", diagnostic: "answer_network_or_timeout",
        result: "BLOCKED" });
      continue;
    }

    const body = response.body;
    const citations = Array.isArray(body.citations) ? body.citations : [];
    const answer = typeof body.answer === "string" ? body.answer : "";
    const isAbstain = body.status === "insufficient_evidence" &&
      citations.length === 0 && !/\[S\d+\]/u.test(answer);
    const fact = item.expected === "answer"
      ? item.answerPattern.test(answer) : "n/a";
    const citation = item.expected === "answer"
      ? citations.length === 1 && citations[0]?.documentTitle === item.document &&
        typeof citations[0]?.sourceId === "string" &&
        answer.includes("[" + citations[0].sourceId + "]")
      : citations.length === 0;
    const correct = item.expected === "answer"
      ? body.status === "answered" && fact && citation
      : isAbstain;

    const diagnostic = response.code ||
      (response.http === 503 && body.message === "Answer service unavailable"
        ? "answer_503_without_reason_header" : body.message || "-");
    let result;
    if (response.http === 429) result = "RATE_LIMITED";
    else if (response.http !== 200) result = "BLOCKED";
    else if (!evidence) result = "NOT_VERIFIABLE";
    else result = correct ? "PASS" : "FAIL";

    rows.push({
      case_id: item.id,
      retrieve_http: retrieved.http,
      sources: sources.length,
      evidence,
      answer_http: response.http,
      status: String(body.status || "-"),
      fact,
      citation,
      diagnostic,
      result,
    });
    console.log(item.id + ": " + result +
      (response.http === 503 ? " (" + diagnostic + ")" : ""));
  }

  console.table(rows);
  const pass = rows.filter(row => row.result === "PASS").length;
  const fail = rows.filter(row => row.result === "FAIL").length;
  const blocked = rows.filter(row => row.result === "BLOCKED" ||
    row.result === "RATE_LIMITED" || row.result === "NOT_VERIFIABLE").length;
  console.log("Phase 3.8 SINGLE-RUN RESULT: " + pass + "/" + count +
    " passed; " + fail + " answer failures; " + blocked +
    " blocked/not verified; complete=" + (rows.length === count) + ".");
  console.log("Evidence=true is a text-presence check, NOT a proof of semantic entailment.");
  if (blocked || fail) {
    console.log("Share this table and any 'DocMind answer diagnostic:' lines from backend CMD.");
    console.log("If diagnostic says answer_503_without_reason_header, restart backend with NODE_ENV=development and DOCMIND_ANSWER_DIAGNOSTICS=1.");
  }
})().catch(() => console.error("Phase 3.8 diagnostic stopped unexpectedly; no document changes were made."));
