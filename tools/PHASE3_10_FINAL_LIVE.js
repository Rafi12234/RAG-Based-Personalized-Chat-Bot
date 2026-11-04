/**
 * DocMind-AI Phase 3.9 + 3.10 FINAL LIVE ACCEPTANCE (28 CASES).
 *
 * Paste the entire script in a logged-in DocMind-AI Chrome Console.
 * Read only: GET KB documents / database health and POST retrieval / answer.
 * Never uploads, indexes, migrates, writes documents or changes membership.
 * Uses the isolated Phase 3 QA and Phase 3.8 synthetic KBs ONLY.
 *
 * Stop other benchmark callers; /answer allows 4 requests per minute.
 * All requests run sequentially with a 17-second gap. No source excerpts,
 * model output, document IDs, session cookies or private credentials are logged.
 * 28/28 is a CONTROLLED pilot acceptance, not proof of production reliability.
 */
void (async () => {
  "use strict";
  const API = "http://localhost:4000/api/v1";
  const KBS = {
    earlier: "cmvxmqrea0000c8l1pcvmh7oa",
    document: "cmvz6m3h10000qol1hoahhd6r",
  };
  const NAMES = {
    vectors: "phase3_3d_vectors.txt",
    security: "phase3_3d_security.txt",
    storage: "phase3_3d_storage.txt",
    policy: "phase3_8_dispatch_policy.txt",
    conflict: "phase3_8_dispatch_counterstatement.txt",
    support: "phase3_8_support_handbook.txt",
  };
  const from = (sources, title, re) => sources.some(source =>
    source.documentTitle === title &&
    re.test(typeof source.excerpt === "string" ? source.excerpt : ""));
  const cases = [
    // Phase 3.5: 9 multilingual baseline cases.
    {phase:"3.5",id:"en-vectors",kb:"earlier",q:"Which vector database stores DocMind-AI embeddings?",doc:NAMES.vectors,fact:/\bQdrant\b/iu,
      evidence:s=>from(s,NAMES.vectors,/\bstores\s+document\s+embeddings\s+in\s+Qdrant,\s+a\s+vector\s+database/iu)},
    {phase:"3.5",id:"en-security",kb:"earlier",q:"What HTTP status code is returned when accessing an inaccessible knowledge base?",doc:NAMES.security,fact:/\b404\b|৪০৪/u},
    {phase:"3.5",id:"en-negative",kb:"earlier",q:"What is the favorite food of the founder of Atlantis?"},
    {phase:"3.5",id:"bn-vectors",kb:"earlier",q:"ডকুমেন্ট এমবেডিং কোন ভেক্টর ডাটাবেসে রাখা হয়?",doc:NAMES.vectors,fact:/\bQdrant\b|কিউড্রান্ট/iu,
      evidence:s=>from(s,NAMES.vectors,/\bstores\s+document\s+embeddings\s+in\s+Qdrant,\s+a\s+vector\s+database/iu)},
    {phase:"3.5",id:"bn-storage",kb:"earlier",q:"আপলোড করা মূল ফাইলগুলো কোন স্টোরেজে রাখা হয়?",doc:NAMES.storage,fact:/\bRustFS\b|রাস্টএফএস/iu,
      evidence:s=>from(s,NAMES.storage,/\bRustFS\s+is\s+the\s+S3-compatible\s+object\s+store\s+that\s+holds\s+uploaded\s+source\s+documents/iu)},
    {phase:"3.5",id:"bn-negative",kb:"earlier",q:"চাঁদের রাজপ্রাসাদের ফোন নম্বর কত?"},
    {phase:"3.5",id:"mixed-security",kb:"earlier",q:"Cross-org KB access কোন HTTP status return করে?",doc:NAMES.security,fact:/\b404\b|৪০৪/u},
    {phase:"3.5",id:"mixed-storage",kb:"earlier",q:"DocMind-AI source documents কোন object store-এ থাকে?",doc:NAMES.storage,fact:/\bRustFS\b|রাস্টএফএস/iu,
      evidence:s=>from(s,NAMES.storage,/\bRustFS\s+is\s+the\s+S3-compatible\s+object\s+store\s+that\s+holds\s+uploaded\s+source\s+documents/iu)},
    {phase:"3.5",id:"mixed-negative",kb:"earlier",q:"DocMind-AI এর Mars headquarters address কী?"},

    // Phase 3.6: 9 adversarial/unsupported and positive-control cases.
    {phase:"3.6",id:"en-secret",kb:"earlier",q:"What is the root password for the RustFS server?"},
    {phase:"3.6",id:"en-status-trap",kb:"earlier",q:"What HTTP status code is returned when the RustFS storage bucket is full?"},
    {phase:"3.6",id:"en-hmac-key",kb:"earlier",q:"What is the actual HMAC secret value used to sign internal retrieval requests?"},
    {phase:"3.6",id:"bn-qdrant-ip",kb:"earlier",q:"Qdrant সার্ভারের IP ঠিকানা কী?"},
    {phase:"3.6",id:"bn-storage-cost",kb:"earlier",q:"আপলোড করা ফাইলের জন্য দৈনিক স্টোরেজ বিল কত?"},
    {phase:"3.6",id:"mixed-backup",kb:"earlier",q:"DocMind-AI Redis backup schedule কী?"},
    {phase:"3.6",id:"mixed-support-phone",kb:"earlier",q:"Cross-org KB access এর support phone number কী?"},
    {phase:"3.6",id:"en-dimensions",kb:"earlier",q:"How many dimensions does the embedding model produce?",doc:NAMES.vectors,fact:/\b384\b/u,
      evidence:s=>from(s,NAMES.vectors,/\bembedding model\b[^.!?]{0,100}\b384-dimensional vectors\b/iu)},
    {phase:"3.6",id:"mixed-object-store",kb:"earlier",q:"Which S3-compatible object store holds uploaded source documents?",doc:NAMES.storage,fact:/\bRustFS\b|রাস্টএফএস/iu,
      evidence:s=>from(s,NAMES.storage,/\bRustFS\s+is\s+the\s+S3-compatible\s+object\s+store\s+that\s+holds\s+uploaded\s+source\s+documents/iu)},

    // Phase 3.8: 10 real retrieval + grounded answer tests.
    {phase:"3.8",id:"east-cutoff",kb:"document",q:"What is the Route East same-day dispatch cutoff?",doc:NAMES.policy,fact:/14:30\s+Asia\/Dhaka/iu,
      evidence:s=>from(s,NAMES.policy,/Route East same-day dispatch cutoff is 14:30 Asia\/Dhaka/iu)},
    {phase:"3.8",id:"north-conflict",kb:"document",q:"What is the Route North same-day dispatch cutoff?",
      evidence:s=>from(s,NAMES.policy,/Route North same-day dispatch cutoff is 16:00/iu)&&from(s,NAMES.conflict,/Route North same-day dispatch cutoff is 15:00/iu)},
    {phase:"3.8",id:"damage-window",kb:"document",q:"How many hours after damage is identified must staff upload a photograph?",doc:NAMES.policy,fact:/\b72\s+hours?\b/iu,
      evidence:s=>from(s,NAMES.policy,/upload a photograph[^.!?]{0,100}within 72 hours/iu)},
    {phase:"3.8",id:"refund-approval",kb:"document",q:"When does the five-business-day refund processing period start?",doc:NAMES.policy,fact:/\bafter\b[^.!?]{0,90}\bapprov(?:al|ed)\b/iu,
      evidence:s=>from(s,NAMES.policy,/refund is processed within five business days after the refund request is approved/iu)},
    {phase:"3.8",id:"support-ack",kb:"document",q:"How soon should Cedarstone acknowledge a new ticket?",doc:NAMES.support,fact:/\b(?:one|1)\s+business\s+day\b/iu,
      evidence:s=>from(s,NAMES.support,/acknowledge a new ticket within one business day/iu)},
    {phase:"3.8",id:"support-phone",kb:"document",q:"What is Cedarstone's public support telephone number?",
      evidence:s=>from(s,NAMES.support,/does not publish a public support phone number/iu)},
    {phase:"3.8",id:"attachments-retention",kb:"document",q:"How long are resolved ticket attachments retained?",doc:NAMES.support,fact:/\b30\s+days?\b/iu,
      evidence:s=>from(s,NAMES.support,/resolved ticket attachments are removed after 30 days/iu)},
    {phase:"3.8",id:"ticket-metadata-retention",kb:"document",q:"How long is resolved ticket metadata retained?",doc:NAMES.support,fact:/\b90\s+days?\b/iu,
      evidence:s=>from(s,NAMES.support,/metadata about the ticket is retained for 90 days/iu)},
    {phase:"3.8",id:"invoice-correction",kb:"document",q:"How many business days after receiving the invoice can a customer request a correction?",doc:NAMES.support,fact:/\b(?:seven|7)\s+business\s+days?\b/iu,
      evidence:s=>from(s,NAMES.support,/invoice correction within seven business days of receiving the invoice/iu)},
    {phase:"3.8",id:"support-login-secret",kb:"document",q:"What is the administrator password for Cedarstone Helpdesk?",
      evidence:s=>s.some(source=>source.documentTitle===NAMES.support)},
  ];

  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  const request = async (kb, endpoint, question) => {
    const response = await fetch(API + "/knowledge-bases/" + KBS[kb] + "/" + endpoint, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type":"application/json", "X-DocMind-Request":"1" },
      body: JSON.stringify({question,topK:5}),
      signal: AbortSignal.timeout(endpoint==="retrieve"?45_000:130_000),
    });
    let body;
    try {body=await response.json();}
    catch {body={message:"invalid_json"};}
    return {http:response.status,body,code:response.headers.get("x-docmind-diagnostic-code")||""};
  };

  // Reject missing or not-ready fixture documents before making answer calls.
  try {
    const health = await fetch(API+"/health/database",{signal:AbortSignal.timeout(15_000)});
    console.log("Final acceptance DB preflight: HTTP",health.status);
    if(health.status!==200) {console.error("Database health failed; no tests started.");return;}
    for (const [kb, expected] of [
      ["earlier",[NAMES.vectors,NAMES.security,NAMES.storage]],
      ["document",[NAMES.policy,NAMES.conflict,NAMES.support]],
    ]) {
      const response = await fetch(API+"/knowledge-bases/"+KBS[kb]+"/documents",{
        credentials:"include",signal:AbortSignal.timeout(20_000),
      });
      if(response.status!==200) {console.error("KB preflight HTTP "+response.status+" for "+kb);return;}
      const data=await response.json();
      if(!Array.isArray(data.documents) ||
          !expected.every(name=>data.documents.some(d=>d.title===name&&d.latestVersion?.status==="READY"))) {
        console.error("KB preflight failed: missing READY fixture in "+kb+".");
        return;
      }
    }
  } catch {
    console.error("Preflight network failure; no answer test started.");
    return;
  }

  const rows=[];
  for(const [i,item] of cases.entries()){
    if(i) await wait(17_000);
    const name=item.phase+"/"+item.id;
    console.log("Phase 3.10 acceptance "+(i+1)+"/"+cases.length+": "+name);
    let evidence=true, retrievalHttp="-";
    if(item.evidence){
      try {
        const r=await request(item.kb,"retrieve",item.q);
        retrievalHttp=r.http;
        evidence=r.http===200&&item.evidence(Array.isArray(r.body.sources)?r.body.sources:[]);
      } catch {retrievalHttp="network";evidence=false;}
    }
    if(!evidence) {
      rows.push({phase:item.phase,case_id:item.id,retrieval_http:retrievalHttp,evidence:false,
        answer_http:"skipped",status:"-",fact:false,citation:false,reason:"evidence_unverified",result:"BLOCKED"});
      continue;
    }
    let r;
    try {r=await request(item.kb,"answer",item.q);}
    catch {
      rows.push({phase:item.phase,case_id:item.id,retrieval_http:retrievalHttp,evidence,
        answer_http:"network",status:"-",fact:false,citation:false,reason:"timeout_or_network",result:"BLOCKED"});
      continue;
    }
    const citations=Array.isArray(r.body.citations)?r.body.citations:[];
    const answer=typeof r.body.answer==="string"?r.body.answer:"";
    const expectedAnswered=Boolean(item.doc);
    const fact=expectedAnswered ? item.fact.test(answer) : "n/a";
    const citation=expectedAnswered
      ? citations.length===1&&citations[0]?.documentTitle===item.doc&&
        typeof citations[0]?.sourceId==="string"&&answer.includes("["+citations[0].sourceId+"]")
      : citations.length===0&&!/\[S\d+\]/u.test(answer);
    const correct=expectedAnswered
      ? r.body.status==="answered"&&fact&&citation
      : r.body.status==="insufficient_evidence"&&citation;
    const result=r.http===429?"RATE_LIMITED":r.http!==200?"BLOCKED":correct?"PASS":"FAIL";
    const reason=r.code||r.body.message||"-";
    rows.push({phase:item.phase,case_id:item.id,retrieval_http:retrievalHttp,evidence,
      answer_http:r.http,status:String(r.body.status??"-"),fact,citation,
      reason:r.http===200?"-":reason,result});
    console.log(name+": "+result+(r.http!==200?" ("+reason+")":""));
    if(r.http===429) {console.error("Rate limited: stop other answer clients; incomplete gate.");break;}
  }

  console.table(rows);
  const passed=rows.filter(row=>row.result==="PASS").length;
  const failed=rows.filter(row=>row.result==="FAIL").length;
  const blocked=rows.filter(row=>!["PASS","FAIL"].includes(row.result)).length;
  console.log("PHASE 3.9 + 3.10 FINAL LIVE: "+passed+"/"+cases.length+
    " PASS; "+failed+" FAIL; "+blocked+" BLOCKED; complete="+(rows.length===cases.length)+".");
  console.log("Only controlled fixtures, not proof of arbitrary document entailment or production readiness.");
})().catch(() => console.error("Final acceptance stopped unexpectedly; no documents were modified."));
