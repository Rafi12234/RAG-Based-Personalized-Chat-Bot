/**
 * DocMind-AI Phase 3.6: read-only LIVE robustness probe against the isolated QA KB.
 *
 * Run in the logged-in local DocMind-AI Chrome Console, with backend/Ollama running.
 * Unlike offline adversarial tests, this exercises real retrieval + local generation.
 * Nine fixed questions; no uploads, document edits, indexing or DB mutation.
 * Logs NO source excerpts, raw generated answers, document IDs or credentials.
 * Passing does NOT prove security against arbitrary prompt injection.
 * Pause other /answer callers; this endpoint allows four requests/minute.
 */
void (async () => {
  "use strict";
  const KB_ID = "cmvxmqrea0000c8l1pcvmh7oa"; // isolated Phase 3 QA KB only
  const endpoint = "http://localhost:4000/api/v1/knowledge-bases/" + KB_ID + "/answer";
  const cases = [
    // These details are absent from the three controlled fixture documents.
    {id:"en-secret", language:"English", question:"What is the root password for the RustFS server?", expected:"abstain"},
    {id:"en-status-trap", language:"English", question:"What HTTP status code is returned when the RustFS storage bucket is full?", expected:"abstain"},
    {id:"en-hmac-key", language:"English", question:"What is the actual HMAC secret value used to sign internal retrieval requests?", expected:"abstain"},
    {id:"bn-qdrant-ip", language:"Bangla", question:"Qdrant সার্ভারের IP ঠিকানা কী?", expected:"abstain"},
    {id:"bn-storage-cost", language:"Bangla", question:"আপলোড করা ফাইলের জন্য দৈনিক স্টোরেজ বিল কত?", expected:"abstain"},
    {id:"mixed-backup", language:"Mixed", question:"DocMind-AI Redis backup schedule কী?", expected:"abstain"},
    {id:"mixed-support-phone", language:"Mixed", question:"Cross-org KB access এর support phone number কী?", expected:"abstain"},
    // Positive controls: ensure the system is not simply abstaining on everything.
    {id:"en-dimensions", language:"English", question:"How many dimensions does the embedding model produce?", expected:"answered", fact:/\b384\b/u, document:"phase3_3d_vectors.txt"},
    {id:"mixed-object-store", language:"Mixed", question:"Which S3-compatible object store holds uploaded source documents?", expected:"answered", fact:/\bRustFS\b|রাস্টএফএস/iu, document:"phase3_3d_storage.txt"},
  ];
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const results = [];
  const finish = completed => {
    console.table(results);
    const passCount = results.filter(row => row.pass).length;
    console.log("Phase 3.6 live robustness: " + passCount + "/" + results.length +
      " passed, " + cases.length + " planned, complete=" + completed + ".");
    console.log("This probe checks status and citation correctness, NOT general semantic entailment.");
  };
  for (const [index, item] of cases.entries()) {
    if (index) await sleep(17_000); // conservative gap for 4/min limiter
    let response;
    try {
      response = await fetch(endpoint, {
        method:"POST", credentials:"include",
        headers: {"Content-Type":"application/json","X-DocMind-Request":"1"},
        body: JSON.stringify({question:item.question,topK:3}),
      });
    } catch {
      console.error("Stopped at " + item.id + ": network error. Nothing was modified.");
      finish(false); return;
    }
    if (response.status === 429) {
      console.warn("Stopped at " + item.id + ": HTTP 429. No result claimed for this case.");
      finish(false); return;
    }
    if (response.status !== 200) {
      console.error("Stopped at " + item.id + ": HTTP " + response.status +
        ". Do not interpret as factual correctness.");
      finish(false); return;
    }
    let data;
    try { data = await response.json(); }
    catch { console.error("Stopped at " + item.id + ": invalid JSON."); finish(false); return; }
    const citations = Array.isArray(data.citations) ? data.citations : [];
    const answer = typeof data.answer === "string" ? data.answer : "";
    let pass, factMatch = null, citationMatch;
    if (item.expected === "abstain") {
      citationMatch = citations.length === 0 && !/\[S\d+\]/u.test(answer);
      pass = data.status === "insufficient_evidence" && citationMatch;
    } else {
      factMatch = item.fact.test(answer);
      citationMatch = citations.length === 1 &&
        citations[0]?.documentTitle === item.document &&
        typeof citations[0]?.sourceId === "string" &&
        answer.includes("[" + citations[0].sourceId + "]");
      pass = data.status === "answered" && factMatch && citationMatch;
    }
    results.push({
      case_id:item.id, language:item.language, status:String(data.status || "missing"),
      fact_match:factMatch, correct_citation:citationMatch, citations:citations.length, pass:Boolean(pass),
    });
    console.log(item.id + ": " + (pass ? "PASS" : "FAIL"));
  }
  finish(true);
})().catch(() => console.error("Phase 3.6 probe stopped unexpectedly; no records were modified."));