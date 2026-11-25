"""Phase 4.8: reproducible span/page retrieval and latency evaluation.

Input records contain only case metadata, evidence IDs and observed timings.
No gold-answer inference, dataset tuning, service calls, or user data writes.
"""
from __future__ import annotations

from math import ceil, isfinite, log2
from statistics import mean
from collections import defaultdict
import argparse
import json
from pathlib import Path
import platform

METHODS=("dense","lexical","hybrid","reranked")
LANGUAGES=("english","bangla","mixed","other")
MAX_CASES=2000


def _percentile(values,p):
    return sorted(values)[max(0,ceil(len(values)*p)-1)] if values else None


def evaluate(cases):
    if not isinstance(cases,list) or not 1<=len(cases)<=MAX_CASES:
        raise ValueError("Invalid case count")
    ids=set()
    groups=defaultdict(list)
    for c in cases:
        if (type(c) is not dict or set(c)!={"id","language","pages","gold","rankings","latency_ms","status"}
                or not isinstance(c["id"],str) or not c["id"] or c["id"] in ids
                or c["language"] not in LANGUAGES or type(c["pages"]) is not int or not 1<=c["pages"]<=5000
                or c["status"] not in ("ok","error")
                or not isinstance(c["gold"],list) or not 1<=len(c["gold"])<=50
                or any(not isinstance(x,str) or not x for x in c["gold"])
                or len(set(c["gold"]))!=len(c["gold"])
                or type(c["rankings"]) is not dict or set(c["rankings"])!=set(METHODS)
                or type(c["latency_ms"]) not in (int,float) or not isfinite(c["latency_ms"])
                or not 0<=c["latency_ms"]<=3_600_000):
            raise ValueError("Malformed evaluation case")
        for ranks in c["rankings"].values():
            if (not isinstance(ranks,list) or len(ranks)>100 or
                    any(not isinstance(x,str) or not x for x in ranks) or len(set(ranks))!=len(ranks)
                    or (c["status"]=="error" and ranks)):
                raise ValueError("Invalid rankings")
        ids.add(c["id"])
        groups["all"].append(c)
        groups["language:"+c["language"]].append(c)
        groups["pages:"+str(c["pages"])].append(c)

    def report(rows):
        result={"cases":len(rows),"errors":sum(c["status"]=="error" for c in rows),
                "latency_ms":{f"p{p}":_percentile([c["latency_ms"] for c in rows],p/100)
                              for p in (50,95,99)}}
        for method in METHODS:
            metrics=defaultdict(list)
            for c in rows:
                ranks=c["rankings"][method]
                gold=set(c["gold"])
                for k in (10,50):
                    top=ranks[:k]
                    hits=sum(x in gold for x in top)
                    metrics[f"recall@{k}"].append(hits/len(gold))
                    metrics[f"all_evidence@{k}"].append(float(hits==len(gold)))
                    ideal=sum(1/log2(i+2) for i in range(min(k,len(gold))))
                    metrics[f"ndcg@{k}"].append(sum(1/log2(i+2) for i,x in enumerate(top) if x in gold)/ideal)
                metrics["mrr"].append(next((1/(i+1) for i,x in enumerate(ranks) if x in gold),0.))
            result[method]={k:mean(v) for k,v in metrics.items()}
        return result
    return {key:report(rows) for key,rows in sorted(groups.items())}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--input",required=True,type=Path)
    args=parser.parse_args()
    with args.input.open("rb") as stream:
        raw=stream.read(16*1024*1024+1)
    if len(raw)>16*1024*1024:
        raise ValueError("Evaluation input too large")
    data=json.loads(raw)
    if type(data) is not dict or set(data)!={"dataset_kind","hardware","model_config","cases"} or data["dataset_kind"] not in ("synthetic_smoke","independent_labeled"):
        raise ValueError("Invalid evaluation envelope")
    if any(not isinstance(data[k],str) or not data[k].strip() or len(data[k])>1000 for k in ("hardware","model_config")):
        raise ValueError("Hardware and model provenance required")
    print(json.dumps({"dataset_kind":data["dataset_kind"],"hardware":data["hardware"],
                      "model_config":data["model_config"],"python":platform.python_version(),
                      "results":evaluate(data["cases"]),"production_acceptance":False},indent=2))


if __name__=="__main__":
    main()
