"""Read-only local model preflight. No DB, S3 or vector writes."""
import json
from .advanced_live_config import selected_kb,embedding_model,answer_models,bundle_key,ENGINE
from .advanced_grounding import prompt


def main():
    if selected_kb() is None:
        raise SystemExit("Advanced pilot disabled")
    try:
        bundle_key()
        e5=embedding_model()
        e5.embed(["query: public synthetic smoke"],batch_size=1)
        reranker,nli,generator=answer_models()
        reranker.score([("test","public test evidence")])
        nli.score([("public test evidence","test evidence")])
        generator(prompt("Return an empty claims array.",[]))
    except Exception as exc:
        raise SystemExit("Advanced model preflight failed: "+type(exc).__name__) from None
    print(json.dumps({"engineVersion":ENGINE,"embedding":e5.pin.digest,
                      "reranker":reranker.pin.digest,"nli":nli.pin.digest,
                      "generator_tokenizer_verified_on_smoke":True,"database_written":False}))


if __name__=="__main__":
    main()
