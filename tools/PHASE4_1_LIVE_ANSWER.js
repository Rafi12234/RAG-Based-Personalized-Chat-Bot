/**
 * Phase 4.1 — live, READ-ONLY grounded-answer screen (32 labeled cases).
 *
 * Run once from a logged-in http://localhost:5173 Chrome DevTools Console.
 * Requires the six dedicated Phase 4.1 synthetic files to be READY in the
 * uniquely named disposable KB. It must NOT touch original Phase 2/3 QA KBs.
 *
 * Browser sends ONLY authenticated POST /answer requests, 18 seconds apart
 * (backend limit: 4 per minute). It does not change documents, versions,
 * vectors, membership, settings, auth or any storage.
 *
 * Automatically checks status, literal reference-answer clues, citation
 * format, and whether cited document titles match labeled gold titles.
 * These are HEURISTIC checks, NOT human verification of factual entailment.
 * Models may correctly paraphrase facts and be marked for review; false
 * positives are possible. Multisource cases deliberately demand both
 * evidence documents. Never treat this as a production-quality score.
 *
 * Console output excludes source excerpts, generated answer texts, secrets,
 * tokens, cookies, knowledge-base and document IDs, and full questions.
 */
void (async () => {
  "use strict";
  const API = "http://localhost:4000/api/v1";
  const KB_NAME = "DocMind Phase4.1 RAG Benchmark - Disposable";
  const DOCUMENTS = ["riverbend_platform.txt","cedarstone_dispatch_policy.txt","cedarstone_north_revision.txt","harbor_finance_rules.txt","aurora_support_rules.txt","shapla_bangla_policy.txt"];
  const EXPECTED_ENGINE = "phase3.10-grounded-2026-11-04-r3";
  const CASES = [{"id":"en-object-store","q":"Which object store preserves the original uploaded files?","language":"english","category":"single_document","gold":["riverbend_platform.txt"],"status":"answered"},{"id":"en-vector-store","q":"Where are the 384-dimensional vectors stored?","language":"english","category":"single_document","gold":["riverbend_platform.txt"],"status":"answered"},{"id":"en-metadata","q":"Which relational database stores memberships and ingestion job state?","language":"english","category":"single_document","gold":["riverbend_platform.txt"],"status":"answered"},{"id":"en-queue","q":"Which service transports ingestion tasks to Celery workers?","language":"english","category":"single_document","gold":["riverbend_platform.txt"],"status":"answered"},{"id":"en-ready","q":"Can archived document versions appear in the live vector index?","language":"english","category":"single_document","gold":["riverbend_platform.txt"],"status":"answered"},{"id":"en-east","q":"What time is Route East's same-day dispatch cutoff?","language":"english","category":"single_document","gold":["cedarstone_dispatch_policy.txt"],"status":"answered"},{"id":"en-south","q":"When is Route South's same-day dispatch cutoff?","language":"english","category":"single_document","gold":["cedarstone_dispatch_policy.txt"],"status":"answered"},{"id":"en-damage","q":"How quickly must Cedarstone staff submit a damage photograph?","language":"english","category":"numeric_temporal","gold":["cedarstone_dispatch_policy.txt"],"status":"answered"},{"id":"en-next-day","q":"What happens to a complete East request submitted after cutoff?","language":"english","category":"single_document","gold":["cedarstone_dispatch_policy.txt"],"status":"answered"},{"id":"en-north-conflict","q":"What is the definitive Route North same-day dispatch cutoff?","language":"english","category":"conflict","gold":["cedarstone_dispatch_policy.txt","cedarstone_north_revision.txt"],"status":"insufficient_evidence"},{"id":"en-bulletin","q":"Was the Route North 15:00 bulletin approved?","language":"english","category":"single_document","gold":["cedarstone_north_revision.txt"],"status":"answered"},{"id":"en-refund","q":"When does Harbor's five-business-day refund processing window begin?","language":"english","category":"numeric_temporal","gold":["harbor_finance_rules.txt"],"status":"answered"},{"id":"en-invoice","q":"How many business days does Harbor give to request invoice correction?","language":"english","category":"numeric_temporal","gold":["harbor_finance_rules.txt"],"status":"answered"},{"id":"en-surcharge","q":"What administration surcharge applies to bulk orders above 80 items?","language":"english","category":"single_document","gold":["harbor_finance_rules.txt"],"status":"answered"},{"id":"en-attachments","q":"How long does Aurora keep resolved ticket attachments?","language":"english","category":"numeric_temporal","gold":["aurora_support_rules.txt"],"status":"answered"},{"id":"en-metadata-retention","q":"How long does Aurora keep resolved ticket metadata?","language":"english","category":"numeric_temporal","gold":["aurora_support_rules.txt"],"status":"answered"},{"id":"en-ticket-ack","q":"How quickly must Aurora acknowledge a newly submitted support ticket?","language":"english","category":"single_document","gold":["aurora_support_rules.txt"],"status":"answered"},{"id":"en-cross-doc","q":"What is Riverbend's vector database and Aurora's resolved attachment retention period?","language":"english","category":"multi_document","gold":["riverbend_platform.txt","aurora_support_rules.txt"],"status":"answered"},{"id":"en-support-unknown","q":"What is Aurora's published public support telephone number?","language":"english","category":"unanswerable","gold":[],"status":"insufficient_evidence"},{"id":"en-finance-unknown","q":"What is Harbor's current bank account number?","language":"english","category":"unanswerable","gold":[],"status":"insufficient_evidence"},{"id":"en-vector-price-unknown","q":"What is the cost per million Qdrant queries?","language":"english","category":"unanswerable","gold":[],"status":"insufficient_evidence"},{"id":"bn-return","q":"শাপলা দোকানে কত দিনের মধ্যে সাধারণ পণ্যের ফেরতের আবেদন করতে হবে?","language":"bangla","category":"single_document","gold":["shapla_bangla_policy.txt"],"status":"answered"},{"id":"bn-damage","q":"শাপলা দোকানে ক্ষতিগ্রস্ত পণ্যের ছবি কত ঘণ্টার মধ্যে জমা দিতে হবে?","language":"bangla","category":"numeric_temporal","gold":["shapla_bangla_policy.txt"],"status":"answered"},{"id":"bn-approval","q":"শাপলা দোকানে ফেরত অনুমোদনের পর কত কর্মদিবসে সিদ্ধান্ত জানাতে হবে?","language":"bangla","category":"numeric_temporal","gold":["shapla_bangla_policy.txt"],"status":"answered"},{"id":"bn-phone","q":"শাপলা গ্রাহক সেবার প্রকাশিত ফোন নম্বর কী?","language":"bangla","category":"unanswerable","gold":[],"status":"insufficient_evidence"},{"id":"bn-east","q":"সিডারস্টোন রুট ইস্টের একই দিনে পাঠানোর শেষ সময় কখন?","language":"bangla","category":"single_document","gold":["cedarstone_dispatch_policy.txt"],"status":"answered"},{"id":"bn-storage","q":"রিভারবেন্ডের আসল আপলোড করা ফাইলগুলো কোন স্টোরেজে থাকে?","language":"bangla","category":"single_document","gold":["riverbend_platform.txt"],"status":"answered"},{"id":"mixed-support","q":"Aurora resolved ticket attachments কত দিন retain করে?","language":"mixed","category":"single_document","gold":["aurora_support_rules.txt"],"status":"answered"},{"id":"mixed-vector","q":"Riverbend document embeddings কোন vector database-এ থাকে?","language":"mixed","category":"single_document","gold":["riverbend_platform.txt"],"status":"answered"},{"id":"mixed-refund","q":"Harbor refund processing window কখন শুরু হয়?","language":"mixed","category":"numeric_temporal","gold":["harbor_finance_rules.txt"],"status":"answered"},{"id":"mixed-multi","q":"Riverbend object store আর Harbor invoice correction time কত?","language":"mixed","category":"multi_document","gold":["riverbend_platform.txt","harbor_finance_rules.txt"],"status":"answered"},{"id":"mixed-unknown","q":"Cedarstone support team-এর private API token কী?","language":"mixed","category":"unanswerable","gold":[],"status":"insufficient_evidence"}];
  const CLUES = {"en-object-store":[["RustFS","রাস্টএফএস"]],"en-vector-store":[["Qdrant","কিউড্রান্ট"]],"en-metadata":[["MySQL"]],"en-queue":[["Redis"]],"en-ready":[["no","not","never","cannot","না","নয়","নেই"]],"en-east":[["14:30","১৪:৩০"]],"en-south":[["11:45","১১:৪৫"]],"en-damage":[["72","৭২"],["hour","ঘণ্টা"]],"en-next-day":[["next","পরবর্তী"],["day","দিন"]],"en-bulletin":[["not","unapproved","no","নয়","না"]],"en-refund":[["approv","অনুমোদ"]],"en-invoice":[["seven","7","৭"],["day","দিন"]],"en-surcharge":[["12","১২"]],"en-attachments":[["30","৩০"]],"en-metadata-retention":[["90","৯০"]],"en-ticket-ack":[["one","1","এক","১"],["day","দিন"]],"en-cross-doc":[["Qdrant","কিউড্রান্ট"],["30","৩০"]],"bn-return":[["14","১৪"],["দিন","day"]],"bn-damage":[["48","৪৮"],["ঘণ্টা","hour"]],"bn-approval":[["তিন","৩","3","three"],["দিন","day"]],"bn-east":[["14:30","১৪:৩০"]],"bn-storage":[["RustFS","রাস্টএফএস"]],"mixed-support":[["30","৩০"]],"mixed-vector":[["Qdrant","কিউড্রান্ট"]],"mixed-refund":[["approv","অনুমোদ"]],"mixed-multi":[["RustFS","রাস্টএফএস"],["seven","7","৭"]]};
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  const mean = values => values.length
    ? Number((values.reduce((a,b)=>a+b,0)/values.length).toFixed(4)) : null;
  const quantile = (values,p) => {
    if(!values.length) return null;
    const sorted=[...values].sort((a,b)=>a-b);
    const pos=(sorted.length-1)*p, lo=Math.floor(pos), hi=Math.min(lo+1,sorted.length-1);
    return Math.round(sorted[lo]+(sorted[hi]-sorted[lo])*(pos-lo));
  };
  const caseClues = (c,text) => {
    const groups=CLUES[c.id];
    return !groups ? null : groups.every(group =>
      group.some(term => text.toLocaleLowerCase().includes(term.toLocaleLowerCase())));
  };
  const get = async path => {
    const response=await fetch(API+path,{credentials:"include",signal:AbortSignal.timeout(20000)});
    return {http:response.status,body:await response.json()};
  };
  try {
    const engine=await get("/answer-engine");
    if(engine.http!==200 || engine.body?.engineVersion!==EXPECTED_ENGINE){
      console.error("Phase 4.1 answer benchmark: updated grounded-answer backend not available. No queries sent.");
      return;
    }
    const orgs=await get("/organizations");
    if(orgs.http!==200||!Array.isArray(orgs.body?.organizations)){
      console.error("Phase 4.1: authenticated workspace list unavailable. No queries sent.");
      return;
    }
    const matches=[];
    for(const org of orgs.body.organizations){
      const listed=await get("/knowledge-bases?organizationId="+encodeURIComponent(org.id));
      if(listed.http!==200||!Array.isArray(listed.body?.knowledgeBases)) continue;
      matches.push(...listed.body.knowledgeBases.filter(kb=>kb.name===KB_NAME));
    }
    if(matches.length!==1){
      console.error("Phase 4.1: expected one accessible disposable KB; found "+matches.length+". No queries sent.");
      return;
    }
    const kb=matches[0], docs=await get("/knowledge-bases/"+encodeURIComponent(kb.id)+"/documents");
    if(docs.http!==200||!Array.isArray(docs.body?.documents)){
      console.error("Phase 4.1: document preflight unavailable. No queries sent.");
      return;
    }
    const active=docs.body.documents.filter(d=>DOCUMENTS.includes(d.title));
    if(active.length!==DOCUMENTS.length || DOCUMENTS.some(name =>
      active.filter(d=>d.title===name&&d.latestVersion?.status==="READY").length!==1)){
      console.error("Phase 4.1: all six uniquely named files must be READY. No queries sent.");
      return;
    }
    console.log("Phase 4.1: 32 grounded-answer queries against disposable KB. 18s pacing; stop other answer clients.");
    const rows=[];
    for(let i=0;i<CASES.length;i++){
      if(i) await wait(18000);
      const c=CASES[i];
      let http="network_error", body={}, elapsed=0;
      const began=performance.now();
      try{
        const response=await fetch(API+"/knowledge-bases/"+encodeURIComponent(kb.id)+"/answer",{
          method:"POST",
          credentials:"include",
          headers:{"Content-Type":"application/json","X-DocMind-Request":"1"},
          body:JSON.stringify({question:c.q,topK:5}),
          signal:AbortSignal.timeout(130000),
        });
        http=response.status;
        body=await response.json();
      } catch { body={}; }
      elapsed=Math.round(performance.now()-began);
      const citations=Array.isArray(body.citations)?body.citations:[];
      const answer=typeof body.answer==="string"?body.answer:"";
      const validLabels=citations.length>0 && citations.every(cit =>
        typeof cit.sourceId==="string" && /^S[1-5]$/.test(cit.sourceId) &&
        answer.includes("["+cit.sourceId+"]") && typeof cit.documentTitle==="string");
      const citedTitles=new Set(citations.map(cit=>cit.documentTitle));
      const gold=c.gold;
      const sourceCheck=c.status==="answered"
        ? validLabels && gold.every(name=>citedTitles.has(name)) &&
            [...citedTitles].every(name=>gold.includes(name))
        : citations.length===0 && !/\[S\d+\]/u.test(answer);
      const statusCheck=body.status===c.status;
      const clueCheck=c.status==="answered"?caseClues(c,answer):null;
      const protocol=http===200 && statusCheck;
      const automatedPass=protocol && sourceCheck && (clueCheck===null || clueCheck===true);
      const result=http===429?"RATE_LIMITED":
        http!==200?"BLOCKED":automatedPass?"HEURISTIC_MATCH":"REVIEW_REQUIRED";
      const row={
        case_id:c.id,language:c.language,category:c.category,
        http,status:typeof body.status==="string"?body.status:"-",
        expected:c.status,status_matches:statusCheck,
        citation_documents_match:sourceCheck,
        answer_clue_match:clueCheck,citations:citations.length,
        elapsed_ms:elapsed,result
      };
      rows.push(row);
      console.log("Phase 4.1 answer "+(i+1)+"/"+CASES.length+": "+c.id+" — "+result);
      if(http===429){console.error("Rate limited: stop other answer clients. Remaining cases not run.");break;}
    }
    console.table(rows);
    const subsets=Object.fromEntries(["english","bangla","mixed"].map(lang=>{
      const rs=rows.filter(r=>r.language===lang);
      return [lang,{completed:rs.length,heuristic_matches:rs.filter(r=>r.result==="HEURISTIC_MATCH").length}];
    }));
    const responded=rows.filter(r=>r.http===200);
    const answerExpected=responded.filter(r=>r.expected==="answered");
    const abstainExpected=responded.filter(r=>r.expected==="insufficient_evidence");
    const summary={
      dataset_kind:"synthetic_documents_live_queries",
      live_answer_calls:true, attempted:rows.length, planned:CASES.length,
      http_200:responded.length, blocked:rows.filter(r=>r.http!==200).length,
      heuristic_matches:rows.filter(r=>r.result==="HEURISTIC_MATCH").length,
      review_required:rows.filter(r=>r.result==="REVIEW_REQUIRED").length,
      answer_status_accuracy:mean(responded.map(r=>Number(r.status_matches))),
      supported_answer_status_accuracy:mean(answerExpected.map(r=>Number(r.status_matches))),
      correct_abstention_rate:mean(abstainExpected.map(r=>Number(r.status_matches))),
      answered_citation_document_match:mean(answerExpected.map(r=>Number(r.citation_documents_match))),
      literal_fact_clue_match:mean(answerExpected.map(r=>Number(r.answer_clue_match))),
      p50_latency_ms:quantile(responded.map(r=>r.elapsed_ms),0.5),
      p95_latency_ms:quantile(responded.map(r=>r.elapsed_ms),0.95),
      by_language:subsets,
      human_fact_entailment_verified:false,
      proof_of_real_world_accuracy:false,
      qa_documents_modified:false
    };
    console.log("PHASE 4.1 LIVE GROUNDED ANSWER PILOT SUMMARY:",summary);
    console.log("Report is a heuristic QA screen. Any actual factual-support claim requires independent human review against source passages.");
  } catch {
    console.error("Phase 4.1 answer preflight could not finish; no document records were modified.");
  }
})();
