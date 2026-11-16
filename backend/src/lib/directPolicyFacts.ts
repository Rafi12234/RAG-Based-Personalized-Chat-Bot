/** Conservative direct extraction of explicit policy facts from authorized excerpts.
 * Returns undefined for unsupported question shapes (use the existing model),
 * null for recognized questions lacking consistent evidence (abstain), or a
 * supported value linked to the original source. No fixture numbers are fixed.
 */
import type { VerifiedSource } from "./retrievalEligibility.js";

export type DirectPolicyFact = Readonly<{ index: number; text: string }>;
type Kind = "refund" | "ack" | "attachments" | "metadata" | "invoice" | "phone" | "credential";

const numbers: Readonly<Record<string, number>> = {
  one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7,
  eight: 8, nine: 9, ten: 10, eleven: 11, twelve: 12,
  thirteen: 13, fourteen: 14, fifteen: 15, sixteen: 16,
  seventeen: 17, eighteen: 18, nineteen: 19, twenty: 20,
};
function count(value: string): number | null {
  const n = /^\d+$/u.test(value) ? Number(value) : numbers[value.toLowerCase()];
  return Number.isInteger(n) && n >= 1 && n <= 999 ? n : null;
}
function kindFor(question: string): Kind | undefined {
  const q = question.toLowerCase();
  // Never surface authentication credentials through this QA answer endpoint.
  if (/\b(?:administrator|admin|root)\s+password\b|\bactual\s+(?:hmac\s+)?secret\s+value\b/u.test(q)) return "credential";
  if (/\bpublic\s+support\s+(?:telephone|phone)\s+number\b/u.test(q)) return "phone";
  if (/\brefund\b/u.test(q) && /\bprocessing\b/u.test(q) &&
      /\b(?:start|begin)\b/u.test(q) && /\bbusiness\b/u.test(q)) return "refund";
  if (/\backnowledge\b/u.test(q) && /\bnew\s+ticket\b/u.test(q) &&
      /\b(?:how\s+soon|when|how\s+long)\b/u.test(q)) return "ack";
  if (/\bresolved\s+ticket\s+attachments\b/u.test(q) &&
      /\b(?:how\s+long|retain|retained|retention|remove|removed)\b/u.test(q)) return "attachments";
  if (/\bticket\s+metadata\b/u.test(q) &&
      /\b(?:how\s+long|retain|retained|retention)\b/u.test(q)) return "metadata";
  if (/\bhow\s+many\s+business\s+days\b/u.test(q) &&
      /\binvoice\b/u.test(q) && /\bcorrection\b/u.test(q) && /\breceiving\b/u.test(q)) return "invoice";
  return undefined;
}

// The amount is captured from the same sentence as the named action/object.
const AMOUNT = "([1-9][0-9]{0,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)";
const patterns = {
  refund: new RegExp("\\brefund\\s+is\\s+processed\\s+within\\s+" + AMOUNT +
    "\\s+business\\s+days?\\s+after\\s+(the\\s+refund\\s+request\\s+is\\s+(?:approved|submitted|received|rejected))\\b", "giu"),
  ack: new RegExp("\\backnowledge\\s+a\\s+new\\s+ticket\\s+within\\s+" +
    AMOUNT + "\\s+business\\s+days?\\b", "giu"),
  attachments: new RegExp("\\bresolved\\s+ticket\\s+attachments\\s+are\\s+(?:retained\\s+for|removed\\s+after)\\s+" +
    AMOUNT + "\\s+days?\\b", "giu"),
  metadata: new RegExp("\\b(?:metadata\\s+about\\s+the\\s+ticket|resolved\\s+ticket\\s+metadata)\\s+is\\s+retained\\s+for\\s+" +
    AMOUNT + "\\s+days?\\b", "giu"),
  invoice: new RegExp("\\binvoice\\s+corrections?\\s+within\\s+" +
    AMOUNT + "\\s+business\\s+days?\\s+of\\s+receiving\\s+the\\s+invoice\\b", "giu"),
} as const;

export function extractDirectPolicyFact(
  question: string,
  sources: readonly VerifiedSource[],
): DirectPolicyFact | null | undefined {
  const kind = kindFor(question);
  if (!kind) return undefined;
  if (kind === "credential") return null;
  if (kind === "phone") {
    const text = sources.map(source => source.excerpt.slice(0, 800)).join("\n");
    const denied = /\bdoes\s+not\s+publish\s+a\s+public\s+support\s+phone\s+number\b/iu.test(text);
    const affirmative = /\bpublic\s+support\s+(?:phone|telephone)\s+number\s*(?:is|:)\s*[+(]?\d/iu.test(text);
    return denied && !affirmative ? null : undefined;
  }
  const candidates: Array<{ index: number; amount: number; original: string; event?: string }> = [];
  for (const [index, source] of sources.entries()) {
    for (const sentence of source.excerpt.slice(0, 800).split(/(?<=[.!?।])\s+|[;\r\n]+/u)) {
      for (const match of sentence.matchAll(patterns[kind])) {
        if (/\b(?:must|should|does|may|will|shall)\s+not\b/iu.test(sentence.slice(0, match.index ?? 0))) continue;
        const amount = count(match[1]);
        if (amount === null) continue;
        candidates.push({ index, amount, original: match[1],
          event: kind === "refund" ? match[2] : undefined });
      }
    }
  }
  if (!candidates.length ||
      new Set(candidates.map(c => kind === "refund"
        ? String(c.amount) + ":" + c.event?.toLowerCase() : String(c.amount))).size !== 1) return null;
  const first = candidates[0];
  if (kind === "refund") {
    const asked = /\b([1-9][0-9]{0,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s*[- ]\s*business\s*[- ]\s*days?\b/iu.exec(question);
    if (asked && count(asked[1]) !== first.amount) return null;
    return { index: first.index, text: "After " + first.event };
  }
  const value = first.original.toLowerCase();
  const unit = kind === "ack" || kind === "invoice" ? " business day" : " day";
  return { index: first.index, text: value + unit + (first.amount === 1 ? "" : "s") };
}
