/** Guarded local advanced mode on the existing authenticated browser routes.
 * No public internal endpoint, silent fallback, or production activation.
 */
import { createHmac, randomBytes } from "node:crypto";
import { z } from "zod";
import type { Request } from "express";
import { prisma } from "../config/prisma.js";
import { HttpError } from "../lib/http.js";
import type { RetrievalRequest } from "../lib/retrievalValidation.js";

export const ADVANCED_ENGINE_VERSION = "phase4.8-advanced-local-v1";
const protectedKBs = new Set(["cmvw9ro3w000548l1gkuh2gx8","cmvxmqrea0000c8l1pcvmh7oa","cmvz6m3h10000qol1hoahhd6r"]);
const id=z.string().regex(/^[a-z0-9]{10,128}$/u);
const digest=z.string().regex(/^[a-f0-9]{64}$/u);
const version=z.string().regex(/^[1-9][0-9]{0,18}$/u);
const proof=z.strictObject({documentId:id,documentVersionId:id,documentTitle:z.string().min(1).max(255),
  generation:digest,revision:version});
const source=z.strictObject({chunkId:z.uuid(),documentId:id,documentVersionId:id,documentTitle:z.string().min(1).max(255),
  chunkIndex:z.number().int().nonnegative(),pageNumber:z.number().int().min(1).max(5000).nullable(),
  sourceLocator:z.string().min(1).max(256),excerpt:z.string().min(1).max(950),rank:z.number().int().min(1).max(10),score:z.null()});
const citation=z.strictObject({sourceId:z.string().regex(/^S(?:[1-9]|1[0-8])$/u),documentId:id,documentVersionId:id,
  documentTitle:z.string().min(1).max(255),chunkId:z.uuid(),pageNumber:z.number().int().min(1).max(5000).nullable(),
  sourceLocator:z.string().min(1).max(256),quote:z.string().min(1).max(950),start:z.number().int().nonnegative(),
  end:z.number().int().positive().max(950),score:z.null()});
const common={engineVersion:z.literal(ADVANCED_ENGINE_VERSION),proofs:z.array(proof).max(8)};
const answers=z.strictObject({...common,status:z.enum(["answered","insufficient_evidence"]),
  answer:z.string().min(1).max(6000),citations:z.array(citation).max(18)});
const retrieval=z.strictObject({...common,status:z.enum(["retrieved","no_relevant_sources"]),sources:z.array(source).max(10)});

type Proof=z.infer<typeof proof>;
type Response=z.infer<typeof answers>|z.infer<typeof retrieval>;
export type AdvancedPublic=Omit<z.infer<typeof answers>,"proofs">|Omit<z.infer<typeof retrieval>,"proofs">;
export type Operation="answer"|"retrieve";
type Scope={id:string;organizationId:string};

export function advancedEnabled(kbId:string):boolean {
  if(process.env.DOCMIND_ENABLE_ADVANCED_LIVE!=="YES_FOR_TESTING") return false;
  const selected=process.env.DOCMIND_ADVANCED_LIVE_KB_ID??"";
  if(process.env.NODE_ENV!=="development"||!id.safeParse(selected).success||protectedKBs.has(selected))
    throw new HttpError(503,"Advanced local pilot configuration is invalid");
  return kbId===selected;
}

export function parseAdvancedResponse(raw:unknown,operation:Operation,topK:number):Response {
  const result=(operation==="answer"?answers:retrieval).safeParse(raw);
  if(!result.success) throw new HttpError(503,"Advanced service returned invalid data");
  const data=result.data;
  const byVersion=new Map(data.proofs.map(p=>[p.documentVersionId,p]));
  if(byVersion.size!==data.proofs.length||new Set(data.proofs.map(p=>p.documentId)).size!==data.proofs.length)
    throw new HttpError(503,"Advanced generation proof is invalid");
  const valid=(s:{documentId:string;documentVersionId:string;documentTitle:string})=>{
    const p=byVersion.get(s.documentVersionId);
    return p?.documentId===s.documentId&&p.documentTitle===s.documentTitle;
  };
  if("citations" in data){
    const ids=new Set(data.citations.map(c=>c.sourceId));
    const markers=[...data.answer.matchAll(/\[S(\d+)\]/gu)].map(m=>"S"+m[1]);
    if(ids.size!==data.citations.length||!data.citations.every(c=>valid(c)&&c.end-c.start===Array.from(c.quote).length)
       ||markers.some(m=>!ids.has(m))||data.citations.some(c=>!markers.includes(c.sourceId))
       ||(data.status==="answered"?!data.citations.length:!!data.citations.length))
      throw new HttpError(503,"Advanced citations failed verification");
  }else{
    if(data.sources.length>topK||new Set(data.sources.map(s=>s.chunkId)).size!==data.sources.length
       ||!data.sources.every((s,i)=>valid(s)&&s.rank===i+1)
       ||(data.status==="retrieved"?!data.sources.length:!!data.sources.length))
      throw new HttpError(503,"Advanced sources failed verification");
  }
  return data;
}

async function finalProofs(req:Request,kb:Scope,proofs:Proof[]){
  const auth=req.auth;
  if(!auth)throw new HttpError(401,"Authentication required");
  const session=await prisma.authSession.findUnique({where:{id:auth.sessionId},
    include:{user:{select:{isActive:true}}}});
  if(!session||session.userId!==auth.userId||session.tokenHash!==auth.tokenHash||
     session.expiresAt<=new Date()||!session.user.isActive) throw new HttpError(401,"Session expired or invalid");
  // Verify the full latest READY document set, not only returned citations.
  // Missing selections or candidates remain NULL and are rejected below.
  const rows=await prisma.$queryRaw<Array<{documentId:string;documentVersionId:string;documentTitle:string;
    generation:string|null;revision:string|null}>>`
    SELECT d.id AS documentId,v.id AS documentVersionId,d.title AS documentTitle,
      g.generationDigest AS generation,CAST(s.revision AS CHAR) AS revision
    FROM Document d JOIN KnowledgeBase kb ON kb.id=d.knowledgeBaseId
    JOIN DocumentVersion v ON v.documentId=d.id
    LEFT JOIN ShadowIndexSelection s ON s.documentVersionId=v.id AND s.queryEnabled=FALSE AND s.revision>0
    LEFT JOIN ShadowIndexCandidate g ON g.documentVersionId=v.id
      AND BINARY g.generationDigest=BINARY s.generationDigest AND g.revokedAt IS NULL
      AND g.modelPinSha256 IS NOT NULL AND g.attemptTokenSha256 IS NOT NULL
      AND BINARY g.documentId=BINARY d.id AND BINARY g.organizationId=BINARY kb.organizationId
      AND BINARY g.knowledgeBaseId=BINARY kb.id
      AND EXISTS(SELECT 1 FROM IngestionJob j WHERE j.id=g.jobId AND j.documentVersionId=v.id
        AND j.status='COMPLETED' AND j.completedAt IS NOT NULL AND j.processingToken IS NULL
        AND j.processingLeaseUntil IS NULL AND j.attempts=g.verifiedAttempt AND j.attempts>0
        AND NOT EXISTS(SELECT 1 FROM IngestionJob other_job WHERE other_job.documentVersionId=v.id AND other_job.id<>j.id))
    WHERE BINARY kb.id=${kb.id} AND BINARY kb.organizationId=${kb.organizationId} AND d.deletedAt IS NULL
      AND v.status='READY' AND NOT EXISTS(SELECT 1 FROM DocumentVersion newer
        WHERE newer.documentId=d.id AND newer.version>v.version)
    ORDER BY BINARY d.id LIMIT 9`;
  const expected=new Map(proofs.map(p=>[p.documentVersionId,p]));
  if(rows.length!==proofs.length||rows.some(r=>{
    const p=expected.get(r.documentVersionId);
    return !p||p.documentId!==r.documentId||p.documentTitle!==r.documentTitle||
      p.generation!==r.generation||p.revision!==r.revision;
  }))throw new HttpError(503,"Advanced document selection changed");
}

export async function fetchAdvanced(req:Request,kb:Scope,input:RetrievalRequest,operation:Operation):Promise<AdvancedPublic>{
  if(!advancedEnabled(kb.id)||!req.auth)throw new HttpError(503,"Advanced local pilot unavailable");
  const secret=process.env.DOCMIND_INTERNAL_RETRIEVAL_SECRET;
  if(!secret||secret.length<32)throw new HttpError(503,"Advanced internal authentication unavailable");
  const body=JSON.stringify({organizationId:kb.organizationId,knowledgeBaseId:kb.id,
    question:input.question,topK:input.topK,operation,userId:req.auth.userId,
    sessionId:req.auth.sessionId,tokenHash:req.auth.tokenHash});
  const timestamp=String(Date.now()),nonce=randomBytes(16).toString("hex");
  const signature=createHmac("sha256",secret).update(`${timestamp}.${nonce}.${body}`,"utf8").digest("hex");
  let data:Response;
  try{
    const res=await fetch("http://127.0.0.1:8000/internal/v2/advanced",{
      method:"POST",headers:{"content-type":"application/json","x-docmind-internal-timestamp":timestamp,
      "x-docmind-internal-nonce":nonce,"x-docmind-internal-signature":signature},body,
      signal:AbortSignal.timeout(180_000),redirect:"error"});
    if(!res.ok||!res.body)throw new Error("Advanced dependency unavailable");
    const reader=res.body.getReader(),chunks:Uint8Array[]=[];
    let size=0;
    while(true){
      const next=await reader.read();
      if(next.done)break;
      size+=next.value.byteLength;
      if(size>512*1024){await reader.cancel();throw new Error("Advanced response too large");}
      chunks.push(next.value);
    }
    data=parseAdvancedResponse(JSON.parse(Buffer.concat(chunks).toString("utf8")),operation,input.topK);
  }catch{
    throw new HttpError(503,"Advanced local engine unavailable; check verified indexing and model configuration");
  }
  await finalProofs(req,kb,data.proofs);
  const {proofs:_proofs,...result}=data;
  return result;
}
