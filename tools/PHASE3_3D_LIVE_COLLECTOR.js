/**
 * Phase 3.3D: run only from Chrome DevTools while logged into your own QA organization.
 * 1) Upload and ingest the THREE controlled QA .txt files into a NEW, isolated QA KB.
 * 2) Replace QA_KB_ID below. Keep it private; do not post raw document IDs.
 * 3) Script makes NINE authorized, read-only /retrieve calls and prints a JSON evaluation dataset.
 *    It does NOT write files, modify records, issue ingestion jobs, or fetch source text.
 * 4) Examine questions/fixtures before treating their ground truth as manually confirmed.
 * 5) Save the JSON privately as quality-fixtures/phase3_3d_live_local.json (ignored? check Git before pushing!).
 * 6) From python-worker: python -m worker.phase3_retrieval_quality --input ..\quality-fixtures\phase3_3d_live_local.json
 */
void (async () => {
  const QA_KB_ID = "REPLACE_WITH_NEW_QA_KB_ID";
  if (!/^c[a-z0-9]{10,}$/i.test(QA_KB_ID)) {
    console.error("Set QA_KB_ID to your newly created QA Knowledge Base ID first.");
    return;
  }
  const api = `http://localhost:4000/api/v1/knowledge-bases/${QA_KB_ID}`;
  const expectedTitles = [
    "phase3_3d_vectors.txt", "phase3_3d_security.txt", "phase3_3d_storage.txt"
  ];
  const list = await fetch(`${api}/documents`, {credentials:"include",headers:{"X-DocMind-Request":"1"}});
  if (!list.ok) { console.error("QA KB document list unavailable",list.status);return; }
  const listing = await list.json();
  const records = Array.isArray(listing.documents) ? listing.documents : [];
  const docIds = new Map();
  for (const title of expectedTitles) {
    const matches = records.filter(d => d.title === title);
    if (matches.length !== 1 || matches[0]?.latestVersion?.status !== "READY") {
      console.error("Missing, duplicated or not-READY QA document:", title);
      return;
    }
    docIds.set(title, matches[0].id);
  }
  // Designed QA questions with explicit expected documents; confirm the labels
  // from the supplied fixtures before using these results as real_labeled.
  const cases = [
    {case_id:"en-vectors",language:"english",title:expectedTitles[0],question:"Which vector database stores DocMind-AI embeddings?"},
    {case_id:"en-security",language:"english",title:expectedTitles[1],question:"What HTTP status is returned for inaccessible knowledge bases?"},
    {case_id:"bn-vectors",language:"bangla",title:expectedTitles[0],question:"ডকুমেন্ট এমবেডিং কোন ভেক্টর ডাটাবেসে রাখা হয়?"},
    {case_id:"bn-storage",language:"bangla",title:expectedTitles[2],question:"আপলোড করা মূল ফাইলগুলো কোন স্টোরেজে রাখা হয়?"},
    {case_id:"mixed-security",language:"mixed",title:expectedTitles[1],question:"Cross-organization KB access কোন HTTP status ফেরত দেয়?"},
    {case_id:"mixed-storage",language:"mixed",title:expectedTitles[2],question:"DocMind-AI source documents কোন object store-এ থাকে?"},
    {case_id:"en-negative",language:"english",title:null,question:"What is the favorite food of the founder of Atlantis?"},
    {case_id:"bn-negative",language:"bangla",title:null,question:"চাঁদের রাজপ্রাসাদের ফোন নম্বর কত?"},
    {case_id:"mixed-negative",language:"mixed",title:null,question:"DocMind-AI এর Mars headquarters address কী?"},
  ];
  const output = [];
  for (const item of cases) {
    const response = await fetch(`${api}/retrieve`, {
      method:"POST", credentials:"include",headers:{"Content-Type":"application/json","X-DocMind-Request":"1"},
      body:JSON.stringify({question:item.question,topK:5}),
    });
    if (!response.ok) { console.error(`Stopped at ${item.case_id}; HTTP ${response.status}. No metrics should be claimed.`);return; }
    const body = await response.json();
    if (!Array.isArray(body.sources)) { console.error("Unexpected retrieval response");return; }
    const results = [];
    for (const source of body.sources) {
      if (typeof source.documentId !== "string" || typeof source.score !== "number") {
        console.error("Malformed source metadata");return;
      }
      // The Python evaluator requires score-ordered document IDs. Keep duplicate
      // chunks: its scorer explicitly avoids counting duplicate documents twice.
      results.push({document_id:source.documentId,score:source.score});
    }
    output.push({case_id:item.case_id,language:item.language,
      expected_document_ids:item.title ? [docIds.get(item.title)] : [],results});
    console.log(`Completed ${item.case_id}; ${results.length} candidate(s).`);
    // Rate limit is 12/min. Stay below it, or rerun after a quiet minute if 429.
    await new Promise(resolve=>setTimeout(resolve,1200));
  }
  const dataset = {dataset_kind:"real_labeled",cases:output};
  console.log("QA dataset JSON (contains document IDs; store locally, do not share publicly):");
  console.log(JSON.stringify(dataset,null,2));
  console.log("Use the saved JSON with phase3_retrieval_quality.py. Claims apply only to this small controlled QA corpus.");
})();
