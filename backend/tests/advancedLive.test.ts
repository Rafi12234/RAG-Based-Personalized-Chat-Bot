import {test} from "node:test";
import assert from "node:assert/strict";
import {once} from "node:events";
import type {AddressInfo} from "node:net";
import express from "express";
import {createRetrievalRouter,type RetrievalRouteDependencies} from "../src/routes/retrieval.js";
import {ADVANCED_ENGINE_VERSION,parseAdvancedResponse} from "../src/services/advancedLive.js";
import {HttpError,errorHandler} from "../src/lib/http.js";

const KB="testkb0000000001",ORG="testorg000000001",DOC="testdoc000000001",VER="testver000000001";
const proof={documentId:DOC,documentVersionId:VER,documentTitle:"Book",generation:"a".repeat(64),revision:"1"};
const citation={sourceId:"S1",documentId:DOC,documentVersionId:VER,documentTitle:"Book",
  chunkId:"123e4567-e89b-42d3-a456-426614174000",pageNumber:5,sourceLocator:"pdf:p00005:b00000",
  quote:"Duration is 12 days.",start:0,end:20,score:null};
const response={engineVersion:ADVANCED_ENGINE_VERSION,proofs:[proof],status:"answered",
  answer:"12 days [S1]",citations:[citation]};

test("advanced response verifies exact selected version, citation marker, quote and rank",()=>{
  assert.equal(parseAdvancedResponse(response,"answer",5).status,"answered");
  for(const bad of [{...response,proofs:[]},{...response,answer:"12 days [S99]"},
    {...response,citations:[{...citation,documentVersionId:"foreign000000001"}]},
    {...response,citations:[{...citation,end:19}]},{...response,proofs:[proof,proof]}]){
    assert.throws(()=>parseAdvancedResponse(bad,"answer",5),HttpError);
  }
});

test("actual browser route selects advanced only for authorized scope and never silently falls back",async()=>{
  let calls=0,revoked=false,fail=false;
  const deps:RetrievalRouteDependencies={
    authenticate(req,_res,next){req.auth={userId:req.header("x-user")??"",sessionId:"session",tokenHash:"hash"};next();},
    identifyUser:req=>req.auth!.userId,
    async authorizeKnowledgeBase(userId,kbId){
      if(userId!=="alice"||kbId!==KB||revoked)throw new HttpError(404,"Resource not found");
      return {kb:{id:KB,organizationId:ORG}};
    },
    searchPoints:async()=>{throw new Error("Unexpected v1 fallback");},
    loadDocuments:async()=>[],
    advancedEnabled:k=>k===KB,
    async advanced(req,kb,input,operation){
      calls++;assert.equal(req.auth?.userId,"alice");assert.equal(kb.organizationId,ORG);
      assert.equal(input.question,"duration");assert.equal(operation,"answer");
      if(fail)throw new HttpError(503,"Advanced service unavailable");
      const {proofs,...data}=parseAdvancedResponse(response,"answer",5);
      return data;
    }
  };
  const app=express();app.use(express.json());app.use("/kb",createRetrievalRouter(deps));app.use(errorHandler);
  const server=app.listen(0,"127.0.0.1");await once(server,"listening");
  const url="http://127.0.0.1:"+(server.address() as AddressInfo).port+"/kb/"+KB+"/answer";
  const post=(user="alice",body={question:"duration",topK:5})=>fetch(url,{method:"POST",
    headers:{"content-type":"application/json","x-user":user},body:JSON.stringify(body)});
  try{
    const r=await post();assert.equal(r.status,200);assert.equal(r.headers.get("cache-control"),"no-store");
    const data=await r.json();assert.equal(data.engineVersion,ADVANCED_ENGINE_VERSION);assert.ok(!("proofs" in data));
    assert.equal((await post("bob")).status,404);assert.equal(calls,1);
    assert.equal((await post("alice",{question:"duration",topK:99})).status,400);assert.equal(calls,1);
    fail=true;assert.equal((await post()).status,503);
  }finally{await new Promise<void>((resolve,reject)=>server.close(e=>e?reject(e):resolve()));}
});
