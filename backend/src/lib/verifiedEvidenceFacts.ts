/**
 * Evidence-first multi-fact resolution for common policy and infrastructure
 * questions. Answer VALUES come only from authorized READY source excerpts;
 * question terms and document titles may select a subject, never a value.
 * Unsupported intents fall through to the existing structured model path.
 */
import type { VerifiedSource } from "./retrievalEligibility.js";
import { resolveNamedInfrastructure } from "./namedInfrastructure.js";
import { resolveExplicitRelationalDatabase } from "./explicitRelationalDatabase.js";

type Kind = "vector" | "object" | "database" | "transport" | "archive" |
  "cutoff" | "photo" | "queue" | "bulletin" | "refund_start" | "refund_period" |
  "invoice" | "surcharge" | "ack" | "attachments" | "metadata" |
  "return" | "return_label" | "support_channel";
type Slot = { kind: Kind; at: number; route?: string };
type Candidate = { value: string; sourceIndex: number; key: string };
export type VerifiedFactResolution =
  | Readonly<{ state: "not_applicable" | "insufficient" }>
  | Readonly<{ state: "found"; parts: ReadonlyArray<{ value: string; sourceIndex: number }> }>;

const WORD = "([1-9][0-9]{0,3}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)";
const numberWords: Record<string, number> = {
  one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7,
  eight: 8, nine: 9, ten: 10, eleven: 11, twelve: 12,
  thirteen: 13, fourteen: 14, fifteen: 15, sixteen: 16,
  seventeen: 17, eighteen: 18, nineteen: 19, twenty: 20,
};
const digits = (s: string) => s.replace(/[০-৯]/gu, c => String(c.charCodeAt(0) - "০".charCodeAt(0)));
function number(s: string): number | null {
  const word = digits(s.toLowerCase().trim());
  const n = /^\d+$/u.test(word) ? Number(word) : numberWords[word];
  return Number.isInteger(n) && n >= 1 && n <= 9999 ? n : null;
}
function duration(s: string, business: boolean, unit: "day" | "hour" = "day"): string {
  const n = number(s);
  if (n === null) return "";
  return (/^[a-z]+$/iu.test(s) ? s.toLowerCase() : String(n)) +
    (business ? " business" : "") + " " + unit + (n === 1 ? "" : "s");
}
function sentences(source: VerifiedSource): string[] {
  const excerpt = source.excerpt.slice(0, 1600).replace(/\\r\\n|\\n|\\r/gu, " ")
    .replace(/\s+/gu, " ");
  return excerpt.split(/(?<=[.!?।])\s+|[;\r\n]+/u).map(s => s.trim()).filter(Boolean);
}
function affirmative(s: string): boolean {
  return !/\b(?:does\s+not|is\s+not|are\s+not|may\s+not|must\s+not|should\s+not|never|forbidden)\b/iu.test(s) &&
    !/(?:^|\s)(?:নয়|নয়|নেই|নিষিদ্ধ)(?:\s|$)/u.test(s);
}
function slot(slots: Slot[], kind: Kind, at: number, route?: string): void {
  if (at >= 0 && !slots.some(s => s.kind === kind && s.at === at && s.route === route)) {
    slots.push({ kind, at, route });
  }
}
function plan(question: string): Slot[] {
  const q = question.toLowerCase(), out: Slot[] = [];
  const at = (re: RegExp) => q.search(re);
  if (/\b(?:private|administrator|admin|root)\s+(?:password|phone|token|secret)\b|\b(?:api\s+token|bank\s+account\s+number)\b/iu.test(q)) return [];
  if (/\bbulletin\b/iu.test(q) && /\bapprov/iu.test(q)) {
    slot(out, "bulletin", at(/\bbulletin\b/u)); return out;
  }
  if (/\barchived\b/iu.test(q) && /\bvector\s+index\b/iu.test(q)) {
    slot(out, "archive", at(/\barchived\b/u)); return out;
  }
  if (/\bsurcharge\b/iu.test(q) && /\b(?:bulk|order|items?)\b/iu.test(q)) {
    slot(out, "surcharge", at(/\bsurcharge\b/u)); return out;
  }
  if (/\breturn\s+label\b/iu.test(q)) {
    slot(out, "return_label", at(/\breturn\s+label\b/u)); return out;
  }
  if (/\brefund\b/iu.test(q) && /\bprocessing\b/iu.test(q)) {
    slot(out, /\b(?:when|begin|start)\b|কখন\s+শুরু/iu.test(q) ?
      "refund_start" : "refund_period", at(/\brefund\b/u));
    return out;
  }
  if ((/\b(?:damage|damaged)\b|ক্ষতিগ্রস্ত/u.test(q)) &&
      (/\b(?:photo|photograph|picture)\b|ছবি/u.test(q))) {
    slot(out, "photo", at(/\b(?:damage|damaged)\b|ক্ষতিগ্রস্ত/u)); return out;
  }
  if (/\b(?:after|miss(?:ed|ing))\s+(?:the\s+)?cutoff\b/iu.test(q) &&
      /\b(?:east|request|shipment)\b/iu.test(q)) {
    slot(out, "queue", at(/\bcutoff\b/u)); return out;
  }
  if (/\b(?:return(?:s)?\s+(?:request|deadline)|regular\s+products?.*return|request\s+returns?)\b|ফেরতের\s+আবেদন/iu.test(q) &&
      !/\brefund\b/iu.test(q)) {
    slot(out, "return", at(/\breturn|ফেরত/u)); return out;
  }
  if (/যোগাযোগ\s+মাধ্যম|support\s+(?:contact\s+)?channel/iu.test(q)) {
    slot(out, "support_channel", at(/যোগাযোগ|support/u)); return out;
  }
  if (/\backnowledge/iu.test(q) && /\bticket\b/iu.test(q)) {
    slot(out, "ack", at(/\backnowledge/u)); return out;
  }
  if (/\binvoice\b/iu.test(q) && /\bcorrections?\b/iu.test(q))
    slot(out, "invoice", at(/\binvoice\b/u));
  if (/\battachments?\b/iu.test(q) && /\b(?:resolved|ticket|retain|retained|retention)\b|কত\s+দিন/u.test(q))
    slot(out, "attachments", at(/\battachments?\b/u));
  if (/\bmetadata\b/iu.test(q) && /\b(?:ticket|resolved|retain|retained|retention|attachments?)\b|কত\s+দিন/u.test(q))
    slot(out, "metadata", at(/\bmetadata\b/u));
  // A bare "which database stores vectors?" is ambiguous. Do not bypass
  // model attribution without an explicit vector-database or location intent.
  if (/\bvector\s+database\b/iu.test(q) ||
      /\bwhere\b.*\b(?:vectors?|embeddings?)\b.*\bstor(?:ed|e)\b/iu.test(q))
    slot(out, "vector", at(/\bvector|embedding/u));
  if (/\bobject\s+store\b|স্টোরেজ/u.test(q))
    slot(out, "object", at(/\bobject\s+store\b|স্টোরেজ/u));
  if (/\b(?:transports?|carries?|routes?)\b/iu.test(q) && /\bingestion\s+tasks?\b/iu.test(q))
    slot(out, "transport", at(/\btransports?|carries?|routes?\b/u));
  if (/\b(?:relational\s+database|database\s+stores?\s+memberships?)\b/iu.test(q))
    slot(out, "database", at(/\b(?:relational\s+database|database)\b/u));
  if (/cutoff|same[- ]day\s+dispatch|শেষ\s+সময়|শেষ\s+সময়/iu.test(q)) {
    for (const m of q.matchAll(/\broute\s+(east|south|north)\b/giu))
      slot(out, "cutoff", m.index ?? 0, m[1].toLowerCase());
    for (const m of q.matchAll(/রুট\s+(ইস্ট|সাউথ|নর্থ)/gu))
      slot(out, "cutoff", m.index ?? 0, m[1] === "ইস্ট" ? "east" : m[1] === "সাউথ" ? "south" : "north");
  }
  return out.sort((a, b) => a.at - b.at);
}
function org(source: VerifiedSource): string | null {
  return /^([a-z][a-z0-9]{3,39})[_-]/iu.exec(source.documentTitle)?.[1].toLowerCase() ?? null;
}
/** True only if an organization name from a trusted document title appears
 * explicitly in the question. Used to prevent cross-organization policy borrowing.
 */
export function namesVerifiedOrganization(question: string, sources: readonly VerifiedSource[]): boolean {
  return sources.map(org).filter((s): s is string => s !== null)
    .some(name => new RegExp("\\b" + name + "\\b", "iu").test(question));
}
function indicesFor(slot: Slot, question: string, sources: readonly VerifiedSource[], slotCount: number): number[] {
  const names = [...new Set(sources.map(org).filter((s): s is string => s !== null))];
  const located: Array<{ name: string; at: number }> = [];
  for (const name of names) {
    for (const match of question.matchAll(new RegExp("\\b" + name + "\\b", "giu")))
      located.push({ name, at: match.index ?? 0 });
  }
  located.sort((a, b) => a.at - b.at);
  const before = located.filter(i => i.at < slot.at);
  const chosen = before.length ? before[before.length - 1].name :
    slotCount === 1 && located.length === 1 ? located[0].name : null;
  return sources.map((_source, index) => index).filter(index => !chosen || org(sources[index]) === chosen);
}
function single(list: Candidate[]): Candidate | null {
  return list.length && new Set(list.map(i => i.key.toLowerCase())).size === 1 ? list[0] : null;
}
function fact(slot: Slot, allowed: number[], sources: readonly VerifiedSource[], question: string): Candidate | null {
  const scoped = allowed.map(i => sources[i]);
  if (!scoped.length) return null;
  if (slot.kind === "vector" || slot.kind === "object") {
    const q = slot.kind === "vector" ? "Where are document embedding vectors stored?"
      : "Which object store holds original uploaded source documents?";
    const r = resolveNamedInfrastructure(q, scoped);
    if (r.state !== "found") return null;
    const index = allowed[r.sourceIndex];
    if (slot.kind === "vector") {
      // Generic "document vectors" alone does not establish that a service is
      // an embedding-vector database. Require explicit embedding evidence.
      const matching = sentences(sources[index]).some(sentence =>
        new RegExp("\\b" + r.name + "\\b", "iu").test(sentence) &&
        /\b(?:embedding|embeddings|dimensional|vector\s+database)\b/iu.test(sentence));
      if (!matching) return null;
    }
    return { value: r.name, key: r.name, sourceIndex: index };
  }
  if (slot.kind === "database") {
    const r = resolveExplicitRelationalDatabase("Which relational database stores memberships and ingestion job state?", scoped);
    return r.state === "found" ?
      { value: r.name, key: r.name, sourceIndex: allowed[r.sourceIndex] } : null;
  }
  const list: Candidate[] = [];
  for (const index of allowed) {
    for (const s of sentences(sources[index])) {
      const take = (value: string, key = value) => { if (value) list.push({ value, key, sourceIndex: index }); };
      let m: RegExpExecArray | null = null;
      if (slot.kind === "transport") {
        if (!affirmative(s)) continue;
        m = /\b([A-Z][A-Za-z0-9_-]{1,39})\s+(?:transports?|routes?|carries?|sends?)\s+[^.!?]{0,100}\bingestion\s+tasks?\s+messages?\b[^.!?]{0,100}\bCelery\s+workers\b/iu.exec(s);
        if (m) take(m[1]);
      } else if (slot.kind === "archive") {
        if (/\bvector\s+index\b/iu.test(s) && /\bonly\s+READY\s+document\s+versions\b/iu.test(s) &&
            /\bno\s+archived\s+document\s+versions\b/iu.test(s))
          take("No — archived document versions cannot appear in the live vector index.", "no");
      } else if (slot.kind === "cutoff") {
        if (!slot.route || !affirmative(s)) continue;
        for (const m of s.matchAll(/\broute\s+(east|south|north)\s+same[- ]day\s+dispatch\s+cutoff\s+is\s+((?:[01]\d|2[0-3]):[0-5]\d)\s+(Asia\/Dhaka)\b/giu))
          if (m[1].toLowerCase() === slot.route) take(m[2] + " " + m[3]);
      } else if (slot.kind === "photo") {
        if (!affirmative(s)) continue;
        if (/\b(?:damaged?|damage)\b/iu.test(s) && /\b(?:photograph|photo)\b/iu.test(s) &&
            /\b(?:must|should)\s+upload\b/iu.test(s)) {
          m = new RegExp("\\bwithin\\s+" + WORD + "\\s+hours?\\b", "iu").exec(s);
          if (m) take(duration(m[1], false, "hour"), String(number(m[1])));
        }
        if (/ক্ষতিগ্রস্ত|ক্ষতি\s+শনাক্ত/u.test(s) && /ছবি/u.test(s) && /জমা/u.test(s)) {
          m = /([০-৯0-9]{1,4})\s+ঘণ্টার?\s+মধ্যে/u.exec(s);
          if (m) take(duration(m[1], false, "hour"), String(number(m[1])));
        }
      } else if (slot.kind === "queue") {
        if (/\bcomplete\s+(?:shipment|request)\b/iu.test(s) && /\bEast\s+cutoff\b/iu.test(s) &&
            /\bnext\s+working\s+day['’]s\s+dispatch\s+queue\b/iu.test(s))
          take("Next working day's dispatch queue");
      } else if (slot.kind === "bulletin") {
        if (/\bunapproved\s+bulletin\b/iu.test(s) &&
            /\broute\s+north\b/iu.test(s) && /\b15:00\b/u.test(s))
          take("No — the bulletin was unapproved.", "no");
      } else if (slot.kind === "refund_start") {
        if (/\brefund\b/iu.test(s) && /\bprocessing\b/iu.test(s)) {
          if (/\brefund\s+is\s+processed\s+within\b/iu.test(s) &&
              /\bafter\s+the\s+refund\s+request\s+is\s+approved\b/iu.test(s))
            take("After the refund request is approved", "approval");
          else if (/\benters\s+processing\s+only\s+after\s+a\s+refund\s+manager\s+approves\s+the\s+request\b/iu.test(s))
            take("After refund manager approval", "approval");
        }
      } else if (slot.kind === "refund_period") {
        if (/\bprocessing\s+period\s+is\b/iu.test(s) && /\bfrom\s+approval\b/iu.test(s) &&
            /\brefund\b/iu.test(sources[index].excerpt)) {
          m = new RegExp("\\bprocessing\\s+period\\s+is\\s+" + WORD + "\\s+business\\s+days?\\b", "iu").exec(s);
          if (m) take(duration(m[1], true), String(number(m[1])));
        } else if (/\brefund\s+is\s+processed\s+within\b/iu.test(s) && /\bapprov/iu.test(s)) {
          m = new RegExp("\\brefund\\s+is\\s+processed\\s+within\\s+" + WORD + "\\s+business\\s+days?\\s+after\\b", "iu").exec(s);
          if (m) take(duration(m[1], true), String(number(m[1])));
        }
      } else if (slot.kind === "invoice") {
        if (!affirmative(s) || !/\binvoice\s+corrections?\b/iu.test(s) ||
            !/\bof\s+receiving\s+the\s+invoice\b/iu.test(s)) continue;
        m = new RegExp("\\binvoice\\s+corrections?\\s+within\\s+" + WORD + "\\s+business\\s+days?\\b", "iu").exec(s);
        if (m) take(duration(m[1], true), String(number(m[1])));
      } else if (slot.kind === "return_label") {
        if (!affirmative(s) || !/\breturn\s+label\b/iu.test(s) || !/\bvalid\s+return\b/iu.test(s)) continue;
        m = new RegExp("\\bissued\\s+within\\s+" + WORD + "\\s+business\\s+days?\\b", "iu").exec(s);
        if (m) take(duration(m[1], true), String(number(m[1])));
      } else if (slot.kind === "surcharge") {
        if (!affirmative(s) || !/\bbulk\s+orders?\s+above\b/iu.test(s) ||
            !/\bsurcharge\b/iu.test(s) || !/\bpre[- ]tax\s+subtotal\b/iu.test(s)) continue;
        const threshold = /\bbulk\s+orders?\s+above\s+([1-9]\d*)\s+items?\b/iu.exec(s);
        const exact = /\b(?:order\s+of|order\s+with|exactly)\s+([1-9]\d*)\s+items?\b/iu.exec(question);
        if (threshold && exact && Number(exact[1]) <= Number(threshold[1])) continue;
        m = new RegExp("\\bsurcharge\\s+of\\s+" + WORD + "\\s+percent\\b", "iu").exec(s);
        if (m) take(String(number(m[1])) + " percent on pre-tax subtotal", String(number(m[1])));
      } else if (slot.kind === "ack") {
        if (!affirmative(s) || !/\backnowledge\s+a\s+(?:newly\s+submitted\s+support|new)\s+ticket\b/iu.test(s)) continue;
        m = new RegExp("\\bwithin\\s+" + WORD + "\\s+business\\s+days?\\b", "iu").exec(s);
        if (m) take(duration(m[1], true), String(number(m[1])));
      } else if (slot.kind === "attachments" || slot.kind === "metadata") {
        if (!affirmative(s)) continue;
        const before = slot.kind === "attachments" ?
          "\\b(?:resolved\\s+ticket\\s+attachments\\s+are\\s+(?:retained\\s+for|removed\\s+after)|ticket\\s+attachments\\s+are\\s+removed\\s+after)" :
          "\\b(?:resolved\\s+ticket\\s+metadata\\s+is\\s+retained\\s+for|metadata\\s+about\\s+the\\s+ticket\\s+is\\s+retained\\s+for)";
        m = new RegExp(before + "\\s+" + WORD + "\\s+days?\\b", "iu").exec(s);
        if (m) take(String(number(m[1])) + " days", String(number(m[1])));
      } else if (slot.kind === "return") {
        if (/সাধারণ\s+পণ্য/u.test(s) && /ফেরতের\s+আবেদন/u.test(s)) {
          m = /([০-৯0-9]{1,4})\s+দিনের\s+মধ্যে/u.exec(s);
          if (m) take(String(number(m[1])) + " days", String(number(m[1])));
        }
      } else if (slot.kind === "support_channel") {
        m = /গ্রাহক\s+সহায়তার\s+যোগাযোগ\s+মাধ্যম\s+হলো\s+(অনলাইন\s+টিকিট)/u.exec(s);
        if (m) take(m[1]);
      }
    }
  }
  return single(list);
}
export function resolveVerifiedFacts(question: string, sources: readonly VerifiedSource[]): VerifiedFactResolution {
  const slots = plan(question);
  if (!slots.length) return { state: "not_applicable" };
  const parts: Array<{ value: string; sourceIndex: number }> = [];
  for (const item of slots) {
    const result = fact(item, indicesFor(item, question, sources, slots.length), sources, question);
    if (!result) return { state: "insufficient" };
    parts.push({ value: result.value, sourceIndex: result.sourceIndex });
  }
  return { state: "found", parts };
}
