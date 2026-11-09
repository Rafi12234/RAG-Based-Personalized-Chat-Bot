/**
 * Phase 4.1 — LIVE READ-ONLY document-level retrieval benchmark (32 cases).
 * Run once from a logged-in http://localhost:5173 browser Console after
 * creating the disposable KB and uploading the six synthetic fixture files.
 * Sends only authorized POST /retrieve queries. Does NOT modify any KB, source
 * file, index, user membership, or document.
 *
 * No raw excerpts, user identity, KB IDs, document IDs, cookie, or questions
 * are printed. Results are *live measurements on synthetic authored docs*,
 * never proof of real-world retrieval or answer accuracy.
 *
 * Read-only preflight stops if fixture is missing/NOT READY, duplicated or
 * unavailable. 6.5-second pacing avoids 12 retrieval/minute API throttle.
 */
void (async () => {
  "use strict";
  const API = "http://localhost:4000/api/v1";
  const KB_NAME = "DocMind Phase4.1 RAG Benchmark - Disposable";
  const FILENAMES = [
  "riverbend_platform.txt",
  "cedarstone_dispatch_policy.txt",
  "cedarstone_north_revision.txt",
  "harbor_finance_rules.txt",
  "aurora_support_rules.txt",
  "shapla_bangla_policy.txt"
];
  const CASES = [
  {
    "id": "en-object-store",
    "q": "Which object store preserves the original uploaded files?",
    "language": "english",
    "category": "single_document",
    "gold": [
      "riverbend_platform.txt"
    ]
  },
  {
    "id": "en-vector-store",
    "q": "Where are the 384-dimensional vectors stored?",
    "language": "english",
    "category": "single_document",
    "gold": [
      "riverbend_platform.txt"
    ]
  },
  {
    "id": "en-metadata",
    "q": "Which relational database stores memberships and ingestion job state?",
    "language": "english",
    "category": "single_document",
    "gold": [
      "riverbend_platform.txt"
    ]
  },
  {
    "id": "en-queue",
    "q": "Which service transports ingestion tasks to Celery workers?",
    "language": "english",
    "category": "single_document",
    "gold": [
      "riverbend_platform.txt"
    ]
  },
  {
    "id": "en-ready",
    "q": "Can archived document versions appear in the live vector index?",
    "language": "english",
    "category": "single_document",
    "gold": [
      "riverbend_platform.txt"
    ]
  },
  {
    "id": "en-east",
    "q": "What time is Route East's same-day dispatch cutoff?",
    "language": "english",
    "category": "single_document",
    "gold": [
      "cedarstone_dispatch_policy.txt"
    ]
  },
  {
    "id": "en-south",
    "q": "When is Route South's same-day dispatch cutoff?",
    "language": "english",
    "category": "single_document",
    "gold": [
      "cedarstone_dispatch_policy.txt"
    ]
  },
  {
    "id": "en-damage",
    "q": "How quickly must Cedarstone staff submit a damage photograph?",
    "language": "english",
    "category": "numeric_temporal",
    "gold": [
      "cedarstone_dispatch_policy.txt"
    ]
  },
  {
    "id": "en-next-day",
    "q": "What happens to a complete East request submitted after cutoff?",
    "language": "english",
    "category": "single_document",
    "gold": [
      "cedarstone_dispatch_policy.txt"
    ]
  },
  {
    "id": "en-north-conflict",
    "q": "What is the definitive Route North same-day dispatch cutoff?",
    "language": "english",
    "category": "conflict",
    "gold": [
      "cedarstone_dispatch_policy.txt",
      "cedarstone_north_revision.txt"
    ]
  },
  {
    "id": "en-bulletin",
    "q": "Was the Route North 15:00 bulletin approved?",
    "language": "english",
    "category": "single_document",
    "gold": [
      "cedarstone_north_revision.txt"
    ]
  },
  {
    "id": "en-refund",
    "q": "When does Harbor's five-business-day refund processing window begin?",
    "language": "english",
    "category": "numeric_temporal",
    "gold": [
      "harbor_finance_rules.txt"
    ]
  },
  {
    "id": "en-invoice",
    "q": "How many business days does Harbor give to request invoice correction?",
    "language": "english",
    "category": "numeric_temporal",
    "gold": [
      "harbor_finance_rules.txt"
    ]
  },
  {
    "id": "en-surcharge",
    "q": "What administration surcharge applies to bulk orders above 80 items?",
    "language": "english",
    "category": "single_document",
    "gold": [
      "harbor_finance_rules.txt"
    ]
  },
  {
    "id": "en-attachments",
    "q": "How long does Aurora keep resolved ticket attachments?",
    "language": "english",
    "category": "numeric_temporal",
    "gold": [
      "aurora_support_rules.txt"
    ]
  },
  {
    "id": "en-metadata-retention",
    "q": "How long does Aurora keep resolved ticket metadata?",
    "language": "english",
    "category": "numeric_temporal",
    "gold": [
      "aurora_support_rules.txt"
    ]
  },
  {
    "id": "en-ticket-ack",
    "q": "How quickly must Aurora acknowledge a newly submitted support ticket?",
    "language": "english",
    "category": "single_document",
    "gold": [
      "aurora_support_rules.txt"
    ]
  },
  {
    "id": "en-cross-doc",
    "q": "What is Riverbend's vector database and Aurora's resolved attachment retention period?",
    "language": "english",
    "category": "multi_document",
    "gold": [
      "riverbend_platform.txt",
      "aurora_support_rules.txt"
    ]
  },
  {
    "id": "en-support-unknown",
    "q": "What is Aurora's published public support telephone number?",
    "language": "english",
    "category": "unanswerable",
    "gold": []
  },
  {
    "id": "en-finance-unknown",
    "q": "What is Harbor's current bank account number?",
    "language": "english",
    "category": "unanswerable",
    "gold": []
  },
  {
    "id": "en-vector-price-unknown",
    "q": "What is the cost per million Qdrant queries?",
    "language": "english",
    "category": "unanswerable",
    "gold": []
  },
  {
    "id": "bn-return",
    "q": "শাপলা দোকানে কত দিনের মধ্যে সাধারণ পণ্যের ফেরতের আবেদন করতে হবে?",
    "language": "bangla",
    "category": "single_document",
    "gold": [
      "shapla_bangla_policy.txt"
    ]
  },
  {
    "id": "bn-damage",
    "q": "শাপলা দোকানে ক্ষতিগ্রস্ত পণ্যের ছবি কত ঘণ্টার মধ্যে জমা দিতে হবে?",
    "language": "bangla",
    "category": "numeric_temporal",
    "gold": [
      "shapla_bangla_policy.txt"
    ]
  },
  {
    "id": "bn-approval",
    "q": "শাপলা দোকানে ফেরত অনুমোদনের পর কত কর্মদিবসে সিদ্ধান্ত জানাতে হবে?",
    "language": "bangla",
    "category": "numeric_temporal",
    "gold": [
      "shapla_bangla_policy.txt"
    ]
  },
  {
    "id": "bn-phone",
    "q": "শাপলা গ্রাহক সেবার প্রকাশিত ফোন নম্বর কী?",
    "language": "bangla",
    "category": "unanswerable",
    "gold": []
  },
  {
    "id": "bn-east",
    "q": "সিডারস্টোন রুট ইস্টের একই দিনে পাঠানোর শেষ সময় কখন?",
    "language": "bangla",
    "category": "single_document",
    "gold": [
      "cedarstone_dispatch_policy.txt"
    ]
  },
  {
    "id": "bn-storage",
    "q": "রিভারবেন্ডের আসল আপলোড করা ফাইলগুলো কোন স্টোরেজে থাকে?",
    "language": "bangla",
    "category": "single_document",
    "gold": [
      "riverbend_platform.txt"
    ]
  },
  {
    "id": "mixed-support",
    "q": "Aurora resolved ticket attachments কত দিন retain করে?",
    "language": "mixed",
    "category": "single_document",
    "gold": [
      "aurora_support_rules.txt"
    ]
  },
  {
    "id": "mixed-vector",
    "q": "Riverbend document embeddings কোন vector database-এ থাকে?",
    "language": "mixed",
    "category": "single_document",
    "gold": [
      "riverbend_platform.txt"
    ]
  },
  {
    "id": "mixed-refund",
    "q": "Harbor refund processing window কখন শুরু হয়?",
    "language": "mixed",
    "category": "numeric_temporal",
    "gold": [
      "harbor_finance_rules.txt"
    ]
  },
  {
    "id": "mixed-multi",
    "q": "Riverbend object store আর Harbor invoice correction time কত?",
    "language": "mixed",
    "category": "multi_document",
    "gold": [
      "riverbend_platform.txt",
      "harbor_finance_rules.txt"
    ]
  },
  {
    "id": "mixed-unknown",
    "q": "Cedarstone support team-এর private API token কী?",
    "language": "mixed",
    "category": "unanswerable",
    "gold": []
  }
];
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  async function getJson(path, options) {
    const response = await fetch(API + path, {
      credentials:"include",
      signal:AbortSignal.timeout(30000),
      ...options
    });
    let body;
    try { body = await response.json(); } catch { body = null; }
    return {status:response.status,body};
  }
  const membership = await getJson("/organizations");
  if (membership.status !== 200 || !Array.isArray(membership.body?.organizations)) {
    console.error("Phase 4.1: cannot list authorized workspaces. Sign in and retry."); return;
  }
  const matches = [];
  for (const org of membership.body.organizations) {
    const listed = await getJson("/knowledge-bases?organizationId=" + encodeURIComponent(org.id));
    if (listed.status !== 200 || !Array.isArray(listed.body?.knowledgeBases)) continue;
    for (const kb of listed.body.knowledgeBases) {
      if (kb.name === KB_NAME) matches.push(kb);
    }
  }
  if (matches.length !== 1) {
    console.error("Phase 4.1: expected exactly one accessible disposable KB named '"+KB_NAME+"'; found "+matches.length+". No queries sent.");
    return;
  }
  const kb = matches[0];
  const list = await getJson("/knowledge-bases/"+encodeURIComponent(kb.id)+"/documents");
  if(list.status!==200 || !Array.isArray(list.body?.documents)) {
    console.error("Phase 4.1: document preflight failed. No queries sent.");return;
  }
  const active = list.body.documents.filter(d=>FILENAMES.includes(d.title));
  const missing = FILENAMES.filter(n=>active.filter(d=>d.title===n&&d.latestVersion?.status==="READY").length!==1);
  if(missing.length || active.length!==FILENAMES.length) {
    console.error("Phase 4.1: all six uniquely named fixtures must be READY. Missing/not-ready:",missing);
    console.log("Phase 4.1: no retrieval queries were sent.");return;
  }
  const rows=[];
  for(let i=0;i<CASES.length;i++){
    if(i) await wait(6500);
    const c=CASES[i];
    let response;
    const start=performance.now();
    try {
      response=await getJson("/knowledge-bases/"+encodeURIComponent(kb.id)+"/retrieve",{
        method:"POST",
        headers:{"Content-Type":"application/json","X-DocMind-Request":"1"},
        body:JSON.stringify({question:c.q,topK:10}),
        signal:AbortSignal.timeout(45000)
      });
    } catch {response={status:"network_error",body:null};}
    const elapsed=Math.round(performance.now()-start);
    const retrieved = response.status===200 && Array.isArray(response.body?.sources) ?
      response.body.sources.map(s=>s.documentTitle).filter(v=>typeof v==="string") : [];
    const unique = [...new Set(retrieved)];
    const gold = new Set(c.gold);
    const at = k=>{
      const first=[...new Set(retrieved.slice(0,k))];
      return gold.size ? first.filter(d=>gold.has(d)).length/gold.size : null;
    };
    rows.push({
      id:c.id,language:c.language,category:c.category,
      http:response.status,received:retrieved.length,top1_hit:gold.size?Number(gold.has(retrieved[0])):null,
      recall_at_5:at(5),recall_at_10:at(10),
      all_gold_at_10:gold.size?at(10)===1:null,
      negative_empty:gold.size?null:retrieved.length===0,
      elapsed_ms:elapsed
    });
    console.log("Phase 4.1 live retrieval "+(i+1)+"/"+CASES.length+": "+c.id+" — HTTP "+response.status);
  }
  const valid=rows.filter(r=>r.http===200);
  const positive=valid.filter(r=>r.recall_at_10!==null);
  const negative=valid.filter(r=>r.negative_empty!==null);
  const mean=(xs)=>xs.length ? Number((xs.reduce((a,b)=>a+b,0)/xs.length).toFixed(4)):null;
  console.table(rows);
  console.log("PHASE 4.1 LIVE RETRIEVAL PILOT SUMMARY:",{
    dataset_kind:"synthetic_documents_live_queries",live_retrieval:true,
    attempted:rows.length,http_200:valid.length,failures:rows.length-valid.length,
    positive_cases:positive.length,negative_cases:negative.length,
    recall_at_5:mean(positive.map(r=>r.recall_at_5)),
    recall_at_10:mean(positive.map(r=>r.recall_at_10)),
    full_gold_coverage_at_10:mean(positive.map(r=>Number(r.all_gold_at_10))),
    negative_empty_retrieval_rate:mean(negative.map(r=>Number(r.negative_empty))),
    p50_latency_ms:(()=>{const v=valid.map(r=>r.elapsed_ms).sort((a,b)=>a-b);return v.length?v[Math.floor((v.length-1)*0.5)]:null})(),
    model_called:false,documents_modified:false
  });
  console.log("This is a synthetic-document LIVE retrieval baseline, not a production accuracy certification. Don't share questions, private text, tokens, or KB IDs.");
})();
