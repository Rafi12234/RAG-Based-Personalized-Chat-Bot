/*
 * Phase 3.3 read-only LIVE retrieval quality smoke test.
 * Paste into the Chrome DevTools Console while LOGGED IN to the ORIGINAL
 * DocMind-AI account at http://localhost:5173 (NOT Incognito test account).
 * Sends THREE read-only retrieval requests (under 12/min rate limit).
 * Logs only status, count, score summary and elapsed time; no source text,
 * titles, chunks, session tokens or document identifiers are printed.
 * It cannot prove Recall@K or tune a relevance cutoff with only one document vector.
 */
void (async () => {
  const kbId = "cmvw9ro3w000548l1gkuh2gx8";
  const questions = [
    { language: "english", question: "Which database stores the document vectors?" },
    { language: "bangla", question: "নথির ভেক্টর কোন ডাটাবেসে সংরক্ষিত হয়?" },
    { language: "mixed", question: "DocMind-AI document vectors কোথায় রাখা হয়?" },
  ];
  const results = [];
  for (const item of questions) {
    const start = performance.now();
    try {
      const response = await fetch(`http://localhost:4000/api/v1/knowledge-bases/${kbId}/retrieve`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json", "X-DocMind-Request": "1" },
        body: JSON.stringify({ question: item.question, topK: 5 }),
      });
      const body = await response.json();
      const sources = Array.isArray(body.sources) ? body.sources : [];
      const scores = sources.map(s => s.score);
      const validScores = scores.every(s => typeof s === "number" && Number.isFinite(s) && s >= -1 && s <= 1);
      const descending = scores.every((s, i) => i === 0 || scores[i - 1] >= s);
      const status = typeof body.status === "string" ? body.status : (body.message ?? "unknown");
      results.push({
        language: item.language, http: response.status, status,
        sources: sources.length, scoreValid: validScores, rankedDescending: descending,
        topScore: scores.length ? Number(scores[0].toFixed(4)) : null,
        elapsedMs: Math.round(performance.now() - start),
      });
    } catch (error) {
      results.push({ language: item.language, http: "network_error", status: "request_failed",
        sources: 0, scoreValid: false, rankedDescending: false, topScore: null,
        elapsedMs: Math.round(performance.now() - start) });
    }
  }
  console.table(results);
  console.log("Phase 3.3 read-only smoke completed. Share ONLY this table, not document contents.");
})();
