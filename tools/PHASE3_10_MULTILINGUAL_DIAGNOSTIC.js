/**
 * Read-only Phase 3.10 FINAL 3-CASE MULTILINGUAL ROOT CAUSE.
 * Logged-in local frontend Chrome F12 Console. No KB/DB/Qdrant writes.
 *
 * Three variants per failing question:
 * (a) exact user question, topK=3
 * (b) exact user question, topK=5
 * (c) canonical English paraphrase, topK=5
 * Then three original-question answers with topK=5.
 *
 * Displays only file titles, numeric scores, matching evidence boolean,
 * response status and diagnostic reason; NO raw excerpts or credentials.
 */
void (async () => {
  "use strict";
  const API="http://localhost:4000/api/v1/knowledge-bases/cmvxmqrea0000c8l1pcvmh7oa/";
  const scenarios=[
    {id:"bn-vectors",q:"ডকুমেন্ট এমবেডিং কোন ভেক্টর ডাটাবেসে রাখা হয়?",
     canonical:"Which vector database stores DocMind-AI document embeddings?",
     file:"phase3_3d_vectors.txt",
     contains:/\b(?:stores\s+document\s+embeddings\s+in\s+Qdrant|Qdrant,\s+a\s+vector\s+database)\b/iu,
     fact:/\bQdrant\b|কিউড্রান্ট/iu},
    {id:"bn-storage",q:"আপলোড করা মূল ফাইলগুলো কোন স্টোরেজে রাখা হয়?",
     canonical:"Which S3-compatible object store holds uploaded source documents?",
     file:"phase3_3d_storage.txt",
     contains:/\bRustFS\s+is\s+the\s+S3-compatible\s+object\s+store\b/iu,
     fact:/\bRustFS\b|রাস্টএফএস/iu},
    {id:"mixed-storage",q:"DocMind-AI source documents কোন object store-এ থাকে?",
     canonical:"Which S3-compatible object store holds uploaded source documents?",
     file:"phase3_3d_storage.txt",
     contains:/\bRustFS\s+is\s+the\s+S3-compatible\s+object\s+store\b/iu,
     fact:/\bRustFS\b|রাস্টএফএস/iu},
  ];
  const req=async (path,q,topK)=> {
    const response=await fetch(API+path,{
      method:"POST",credentials:"include",
      headers:{"Content-Type":"application/json","X-DocMind-Request":"1"},
      body:JSON.stringify({question:q,topK}),
      signal:AbortSignal.timeout(path==="answer"?130000:40000),
    });
    return {http:response.status,
      code:response.headers.get("x-docmind-diagnostic-code")||"",
      data:await response.json()};
  };
  const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  const out=[];
  for(const item of scenarios){
    for(const [variant,q,topK] of [
      ["original_k3",item.q,3],
      ["original_k5",item.q,5],
      ["english_k5",item.canonical,5],
    ]){
      try{
        const r=await req("retrieve",q,topK);
        const sources=Array.isArray(r.data.sources)?r.data.sources:[];
        out.push({case_id:item.id,variant,http:r.http,
          sources:sources.length,
          expected_file:sources.some(s=>s.documentTitle===item.file),
          expected_fact:sources.some(s=>s.documentTitle===item.file&&
            item.contains.test(typeof s.excerpt==="string"?s.excerpt:"")),
          files:sources.map(s=>s.documentTitle).join(" | "),
          diagnostic:r.code||r.data.message||"-"});
      }catch{
        out.push({case_id:item.id,variant,http:"network",sources:0,
          expected_file:false,expected_fact:false,files:"-",diagnostic:"network_or_timeout"});
      }
    }
  }
  console.log("PHASE 3.10 MULTILINGUAL RETRIEVAL ROOT CAUSE (no raw source text):");
  console.table(out);

  const answers=[];
  for(const [i,item] of scenarios.entries()){
    if(i)await sleep(17000);
    try{
      const r=await req("answer",item.q,5);
      const citations=Array.isArray(r.data.citations)?r.data.citations:[];
      const answer=typeof r.data.answer==="string"?r.data.answer:"";
      const passed=r.http===200&&r.data.status==="answered"&&
        item.fact.test(answer)&&citations.length===1&&
        citations[0].documentTitle===item.file&&
        answer.includes("["+citations[0].sourceId+"]");
      answers.push({case_id:item.id,http:r.http,status:r.data.status||"-",
        factual_answer:item.fact.test(answer),
        correct_citation:citations.length===1&&citations[0].documentTitle===item.file,
        diagnostic:r.code||r.data.message||"-",
        pass:passed});
    }catch{
      answers.push({case_id:item.id,http:"network",status:"-",
        factual_answer:false,correct_citation:false,diagnostic:"network_or_timeout",pass:false});
    }
  }
  console.log("PHASE 3.10 TOPK5 RECHECK:");
  console.table(answers);
  console.log("3-case topK5 result: "+answers.filter(r=>r.pass).length+"/3 passed. "+
    "Inspect expected_fact in the retrieval table to distinguish missing evidence from an answer fallback issue.");
})().catch(()=>console.error("Multilingual diagnostic aborted; no writes were attempted."));
