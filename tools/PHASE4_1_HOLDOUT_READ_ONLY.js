/*
DocMind-AI Phase 4.1 small, FROZEN QUESTION HOLDOUT v1.
12 never-before-run questions over the existing six public synthetic documents.
Run only from logged-in http://localhost:5173 after selecting the EXACT disposable KB.
Checks are heuristic; answer correctness and citations still need manual review.
Preflight uses GET; tests use only POST /answer. NO document/KB writes.
This is NOT a substitute for an independent new-document corpus.
CSV is automatically downloaded when the run finishes (or stops early).
Results are also available as DocMindHoldout.results until the tab reloads.
*/
(async () => {
  "use strict";
  const KB_NAME = "DocMind Phase4.1 RAG Benchmark - Disposable";
  const ENGINE = "phase4.1-grounded-2026-11-16-r6";
  const API = "http://localhost:4000/api/v1";
  const GAP_MS = 19000;
  const TIMEOUT_MS = 125000;
  const DOCUMENTS = ["riverbend_platform.txt", "cedarstone_dispatch_policy.txt",
    "cedarstone_north_revision.txt", "harbor_finance_rules.txt",
    "aurora_support_rules.txt", "shapla_bangla_policy.txt"];
  const CASES = [
  {
    "id": "h01-embedding-model",
    "language": "en",
    "category": "single_document",
    "question": "Which model does Riverbend apply to both document chunks and user questions?",
    "expectedStatus": "answered",
    "expectedSources": [
      "riverbend_platform.txt"
    ],
    "answerPatterns": [
      "\\b(?:multilingual\\s+)?e5\\b"
    ]
  },
  {
    "id": "h02-store-not-vectors",
    "language": "en",
    "category": "single_document",
    "question": "Does Riverbend keep its dense embedding vectors inside RustFS?",
    "expectedStatus": "answered",
    "expectedSources": [
      "riverbend_platform.txt"
    ],
    "answerPatterns": [
      "\\b(?:no|not|doesn.t|never)\\b"
    ]
  },
  {
    "id": "h03-cutoff-meaning",
    "language": "en",
    "category": "single_document",
    "question": "For Cedarstone Route East, what event does the dispatch cutoff time actually refer to?",
    "expectedStatus": "answered",
    "expectedSources": [
      "cedarstone_dispatch_policy.txt"
    ],
    "answerPatterns": [
      "(?:receipt|receiv|complete shipping request|completed shipping request)"
    ]
  },
  {
    "id": "h04-bulletin-proposal",
    "language": "en",
    "category": "single_document",
    "question": "Which cutoff time does Cedarstone's unapproved North bulletin propose?",
    "expectedStatus": "answered",
    "expectedSources": [
      "cedarstone_north_revision.txt"
    ],
    "answerPatterns": [
      "15\\s*:\\s*00"
    ]
  },
  {
    "id": "h05-escalation-attempts",
    "language": "en",
    "category": "numeric_temporal",
    "question": "After how many unsuccessful contact attempts on separate business days does Aurora escalate to a human supervisor?",
    "expectedStatus": "answered",
    "expectedSources": [
      "aurora_support_rules.txt"
    ],
    "answerPatterns": [
      "\\b(?:two|2)\\b"
    ]
  },
  {
    "id": "h06-return-label-trigger",
    "language": "en",
    "category": "single_document",
    "question": "What acknowledgement starts Harbor's return-label issuance window?",
    "expectedStatus": "answered",
    "expectedSources": [
      "harbor_finance_rules.txt"
    ],
    "answerPatterns": [
      "(?:manager|valid\\s+return|acknowledg)"
    ]
  },
  {
    "id": "h07-shapla-working-days",
    "language": "en",
    "category": "multilingual",
    "question": "Once a Shapla return request is approved, how many working days does the policy allow to notify the customer?",
    "expectedStatus": "answered",
    "expectedSources": [
      "shapla_bangla_policy.txt"
    ],
    "answerPatterns": [
      "(?:three|3|তিন)\\s*(?:working|business|কর্মদিবস)"
    ]
  },
  {
    "id": "h08-two-doc-model-retention",
    "language": "en",
    "category": "multi_document",
    "question": "Name the embedding model used by Riverbend and the retention time for Aurora ticket metadata.",
    "expectedStatus": "answered",
    "expectedSources": [
      "riverbend_platform.txt",
      "aurora_support_rules.txt"
    ],
    "answerPatterns": [
      "\\b(?:multilingual\\s+)?e5\\b",
      "90\\s*(?:days?|দিন)"
    ],
    "claimSources": [
      {
        "pattern": "\\be5\\b",
        "file": "riverbend_platform.txt"
      },
      {
        "pattern": "90\\s*days?",
        "file": "aurora_support_rules.txt"
      }
    ]
  },
  {
    "id": "h09-unknown-vehicle-departure",
    "language": "en",
    "category": "unanswerable",
    "question": "When does Cedarstone Route South's delivery vehicle physically depart?",
    "expectedStatus": "insufficient_evidence",
    "expectedSources": [],
    "answerPatterns": []
  },
  {
    "id": "h10-unknown-support-email",
    "language": "en",
    "category": "unanswerable",
    "question": "What publicly listed email address can Aurora support be reached at?",
    "expectedStatus": "insufficient_evidence",
    "expectedSources": [],
    "answerPatterns": []
  },
  {
    "id": "h11-cross-policy-refund",
    "language": "en",
    "category": "cross_entity",
    "question": "How long does Cedarstone take to process a refund after manager approval?",
    "expectedStatus": "insufficient_evidence",
    "expectedSources": [],
    "answerPatterns": []
  },
  {
    "id": "h12-north-definitive-conflict",
    "language": "en",
    "category": "conflict",
    "question": "Taking both Cedarstone North documents into account, what single authoritative same-day cutoff should be applied?",
    "expectedStatus": "insufficient_evidence",
    "expectedSources": [],
    "answerPatterns": []
  }
];
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const norm = x => String(x ?? "").replace(/[০-৯]/gu, c =>
    String("০১২৩৪৫৬৭৮৯".indexOf(c))).replace(/\s+/gu, " ").trim();
  const titleOf = x => String(x?.documentTitle ?? "").trim().toLowerCase();
  const sorted = x => x.map(s => String(s).toLowerCase()).sort();
  const same = (a, b) => a.length === b.length && a.every((v, i) => v === b[i]);
  const quote = x => '"' + String(x ?? "").replace(/"/g, '""') + '"';
  if (!["localhost", "127.0.0.1"].includes(location.hostname)) {
    console.error("SAFETY STOP: use the localhost DocMind frontend."); return;
  }
  if (globalThis.DocMindHoldout?.running) {
    console.error("Holdout is already running; use DocMindHoldout.stop() first."); return;
  }
  const state = { results: [], running: true, stopRequested: false,
    cases: CASES, startedAt: new Date().toISOString() };
  const columns = ["id","language","category","verdict","problem","question",
    "expectedStatus","actualStatus","expectedSources","actualSources","actualAnswer",
    "httpStatus","diagnostic","elapsedMs","engineVersion"];
  const csv = () => [columns.join(","),...state.results.map(r =>
    columns.map(k => quote(r[k])).join(","))].join("\r\n");
  function downloadCsv() {
    const blob = new Blob(["\uFEFF", csv()], {type:"text/csv;charset=utf-8"});
    const url = URL.createObjectURL(blob), a = document.createElement("a");
    a.href = url; a.download = "docmind_phase4_1_holdout_v1.csv";
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  globalThis.DocMindHoldout = {
    get running() {return state.running;},
    get results() {return state.results;},
    get cases() {return CASES;},
    stop() {state.stopRequested = true; console.warn("Stop requested after current answer.");},
    problems() {return state.results.filter(x => x.verdict !== "PASS");},
    downloadCsv,
    csv
  };
  async function request(path, options = {}) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort("Holdout request timeout"), TIMEOUT_MS);
    try {
      const res = await fetch(API + path, { ...options, signal:ctrl.signal,
        credentials:"include", headers:{
          "X-DocMind-Request":"1",
          ...(options.body ? {"Content-Type":"application/json"} : {}),
          ...(options.headers ?? {})
        }});
      return { httpStatus:res.status, headers:res.headers,
        body:await res.json().catch(() => ({})) };
    } finally {clearTimeout(timer);}
  }
  async function preflight() {
    const heading = document.querySelector(".topbar h1")?.textContent?.trim();
    if (heading !== KB_NAME) throw new Error("SAFETY STOP: select the exact disposable KB.");
    const organizationId = document.querySelector("aside.sidebar .side-label select")?.value;
    if (!organizationId) throw new Error("SAFETY STOP: workspace not identified.");
    const engine = await request("/answer-engine");
    if (engine.httpStatus !== 200 || engine.body?.engineVersion !== ENGINE)
      throw new Error("SAFETY STOP: expected r6 backend engine, or session expired.");
    const list = await request("/knowledge-bases?organizationId=" + encodeURIComponent(organizationId));
    if (list.httpStatus !== 200) throw new Error("SAFETY STOP: KB list unavailable.");
    const matches = (list.body?.knowledgeBases ?? []).filter(k => k.name === KB_NAME);
    if (matches.length !== 1) throw new Error("SAFETY STOP: exact disposable KB not unique.");
    const kbId = matches[0].id;
    const docs = await request("/knowledge-bases/" + encodeURIComponent(kbId) + "/documents");
    if (docs.httpStatus !== 200) throw new Error("SAFETY STOP: document check unavailable.");
    const found = (docs.body?.documents ?? []).filter(x => x.latestVersion?.status === "READY")
      .map(x => x.latestVersion.filename);
    if (docs.body?.documents?.length !== 6 || !same(sorted(found), sorted(DOCUMENTS)))
      throw new Error("SAFETY STOP: disposable KB must have exactly six expected READY docs.");
    return kbId;
  }
  function grade(c, res, elapsedMs) {
    const data = res.body ?? {}, citations = Array.isArray(data.citations) ? data.citations : [];
    const actualSources = [...new Set(citations.map(titleOf))].sort();
    const expectedSources = sorted(c.expectedSources);
    const answer = String(data.answer ?? ""), issues = [];
    let verdict = "PASS";
    if (res.httpStatus !== 200 || data.success !== true) {
      verdict = "ERROR"; issues.push("HTTP " + res.httpStatus);
    } else {
      if (data.status !== c.expectedStatus) {
        verdict = "MISMATCH"; issues.push("Expected " + c.expectedStatus + "; got " + data.status);
      }
      if (c.expectedStatus === "insufficient_evidence") {
        if (citations.length) {verdict="MISMATCH"; issues.push("Abstention has citations");}
      } else {
        if (!same(actualSources,expectedSources) ||
            citations.length !== c.expectedSources.length) {
          verdict="MISMATCH"; issues.push("Wrong or incomplete source citations");
        }
        for (const pattern of c.answerPatterns) {
          if (!new RegExp(pattern,"iu").test(norm(answer))) {
            issues.push("Answer phrase requires human review: " + pattern);
            if (verdict==="PASS") verdict="REVIEW";
          }
        }
        const sourceIds = new Map(citations.map(x => [x.sourceId,titleOf(x)]));
        for (const claim of c.claimSources ?? []) {
          const match = new RegExp("(?:\\b"+claim.pattern+")[^\\[\\]\\n]{0,70}\\[S(\\d+)\\]","iu")
            .exec(norm(answer));
          if (!match) {
            issues.push("Claim-to-citation mapping needs review: " + claim.file);
            if (verdict==="PASS") verdict="REVIEW";
          } else if (sourceIds.get("S"+match[1]) !== claim.file.toLowerCase()) {
            verdict="MISMATCH"; issues.push("Wrong claim-to-document mapping");
          }
        }
      }
    }
    return { id:c.id,language:c.language,category:c.category,verdict,
      problem:issues.join(" | "),question:c.question,expectedStatus:c.expectedStatus,
      actualStatus:data.status ?? "",expectedSources:c.expectedSources.join(" + "),
      actualSources:actualSources.join(" + "),actualAnswer:answer,
      httpStatus:res.httpStatus,diagnostic:data.diagnostic ?? "",
      elapsedMs:Math.round(elapsedMs),engineVersion:data.engineVersion ?? "" };
  }
  async function one(kbId, c) {
    const options = {method:"POST",body:JSON.stringify({question:c.question,topK:5})};
    const path = "/knowledge-bases/" + encodeURIComponent(kbId) + "/answer";
    const started = performance.now();
    let response = await request(path,options);
    if (response.httpStatus===429 && !state.stopRequested) {
      const retryAfter = Number(response.headers.get("retry-after"));
      const delay = Math.max(65000,(Number.isFinite(retryAfter)?retryAfter*1000:0)+2000);
      console.warn("429 rate limit; pausing for one retry");
      await sleep(delay);
      response=await request(path,options);
    }
    return grade(c,response,performance.now()-started);
  }
  try {
    const kbId = await preflight();
    console.log("DocMind frozen holdout v1: 12 NEW questions (not the previous 57)");
    console.log("READ-ONLY; r6; exact disposable KB. Auto-save CSV at end.");
    let errors=0;
    for (let i=0;i<CASES.length;i++) {
      if(state.stopRequested) break;
      const c=CASES[i], started=performance.now();
      let result;
      try {result=await one(kbId,c);}
      catch(err) {
        result={id:c.id,language:c.language,category:c.category,
          verdict:"ERROR",problem:String(err?.message??err),
          question:c.question,expectedStatus:c.expectedStatus,actualStatus:"",
          expectedSources:c.expectedSources.join(" + "),actualSources:"",
          actualAnswer:"",httpStatus:"",diagnostic:"",
          elapsedMs:Math.round(performance.now()-started),engineVersion:""};
      }
      state.results.push(result);
      console.log("["+(i+1)+"/"+CASES.length+"] "+result.verdict+" "+c.id,
        result.problem || (result.actualAnswer+" | "+result.actualSources));
      errors = result.verdict==="ERROR" ? errors+1 : 0;
      if ([401,403,404].includes(result.httpStatus)) {
        console.error("SAFETY STOP: session or KB authorization changed.");break;
      }
      if(errors>=2) {console.error("SAFETY STOP: two consecutive request errors.");break;}
      if(i+1<CASES.length && !state.stopRequested)
        await sleep(result.verdict==="ERROR" ? Math.max(GAP_MS,45000) : GAP_MS);
    }
  } catch(err) {console.error("Holdout cannot start: "+String(err?.message??err));}
  finally {
    state.running=false;
    const totals=state.results.reduce((a,x)=>(a[x.verdict]=(a[x.verdict]||0)+1,a),{});
    console.log("Holdout completed:",state.results.length+"/"+CASES.length,totals);
    if(state.results.length) {
      try {downloadCsv();console.log("CSV automatically exported: docmind_phase4_1_holdout_v1.csv");}
      catch(err) {console.warn("CSV download blocked; run DocMindHoldout.downloadCsv()");}
      const problems=state.results.filter(x=>x.verdict!=="PASS");
      if(problems.length) console.table(problems.map(x=>({
        id:x.id,verdict:x.verdict,problem:x.problem,expected:x.expectedStatus,
        actual:x.actualStatus,answer:x.actualAnswer,source:x.actualSources
      })));
    }
    console.log("Note: pattern/citation checks do not establish semantic entailment.");
  }
})();
