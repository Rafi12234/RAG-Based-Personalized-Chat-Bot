/** Phase 3.4 read-only answer smoke. Paste in original logged-in Chrome app Console.
 * Change KB_ID to your NEW QA KB after ingesting the controlled fixture documents.
 * Log only HTTP status, answer status, citations count and safe model metadata.
 * Do not paste raw private answer/source text into a public chat.
 */
void (async()=>{
  const KB_ID="cmvxmqrea0000c8l1pcvmh7oa";
  if(!/^c[a-z0-9]{10,}$/i.test(KB_ID)){console.error("Set your QA KB ID first");return;}
  const questions=[
    {language:"English",question:"Where are document vectors stored?"},
    {language:"Bangla",question:"আপলোড করা মূল ফাইল কোথায় থাকে?"},
    {language:"Mixed",question:"Cross-org KB access কোন status return করে?"},
    {language:"Unanswerable",question:"What is the favorite food of the founder of Atlantis?"},
  ];
  const results=[];
  for(const item of questions){
    const resp=await fetch(`http://localhost:4000/api/v1/knowledge-bases/${KB_ID}/answer`,{
      method:"POST",credentials:"include",
      headers:{"Content-Type":"application/json","X-DocMind-Request":"1"},
      body:JSON.stringify({question:item.question,topK:5}),
    });
    const data=await resp.json();
    results.push({language:item.language,http:resp.status,status:data.status||data.message,
      citationCount:Array.isArray(data.citations)?data.citations.length:0,
      answerHasCitation:typeof data.answer==="string"&&/\[S\d+\]/.test(data.answer)});
  }
  console.table(results);
})();
