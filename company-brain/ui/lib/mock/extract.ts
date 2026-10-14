// Heuristic stand-ins for the backend's Claude + Voyage calls, used by the
// mock backend. Nothing here is clever: it pattern-matches pasted source
// material into the WORKFLOW_SCHEMA shape and scores relevance by keyword
// overlap. Good enough to make the dashboard interactive without an LLM.

export type WorkflowStep = { step: number; action: string; owner: string; notes: string };

export type ExtractedWorkflow = {
  process: string;
  description: string;
  trigger: string;
  steps: WorkflowStep[];
  decision_rules: string[];
  approvals: string[];
  exceptions: string[];
  sources: string[];
};

const MAX_LINE = 220;

const STOPWORDS = new Set(
  (
    "a an and are as at be but by can do does for from has have how i if in into is it its " +
    "me my not of on or our so that the their them then there this to us was we what when " +
    "which who will with you your should would could any all after before over under more " +
    "than about need needs get got"
  ).split(" "),
);

const ROLE_PATTERNS: [RegExp, string][] = [
  [/\bcs lead\b|\bcustomer success lead\b/i, "CS lead"],
  [/\bon-?call\b/i, "On-call engineer"],
  [/\bincident commander\b/i, "Incident commander"],
  [/\bcfo\b|\bfinance\b/i, "Finance"],
  [/\bcto\b/i, "CTO"],
  [/\bceo\b/i, "CEO"],
  [/\bengineering manager\b|\bmanager\b/i, "Manager"],
  [/\bbuddy\b|\bmentor\b/i, "Onboarding buddy"],
  [/\bhr\b|\bpeople team\b/i, "People team"],
  [/\bit\b|\blaptop\b/i, "IT"],
  [/\bsupport\b|\bcs rep\b|\bagent\b/i, "Support rep"],
  [/\bengineer\b|\bdeploy\b|\brollback\b/i, "Engineer"],
  [/\bcustomer\b/i, "Support rep"],
];

function clean(line: string): string {
  return line
    .replace(/^\s*(?:\d+[.)]|[-*•>]|#+)\s*/, "")
    .replace(/\*\*|__|`/g, "")
    .replace(/\[(\d{1,2}:\d{2})\]\s*/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, MAX_LINE);
}

function sentences(text: string): string[] {
  return text
    .split(/\n+/)
    .flatMap((l) => l.split(/(?<=[.!?])\s+(?=[A-Z])/))
    .map(clean)
    .filter((s) => s.length >= 25 && /[a-z]/.test(s) && !/^={2,}|^-{3,}/.test(s));
}

function ownerFor(line: string): string {
  for (const [re, role] of ROLE_PATTERNS) if (re.test(line)) return role;
  return "Process owner";
}

function take(lines: string[], re: RegExp, used: Set<string>, max: number): string[] {
  const out: string[] = [];
  for (const l of lines) {
    if (out.length >= max) break;
    if (used.has(l) || !re.test(l)) continue;
    used.add(l);
    out.push(/[.!?]$/.test(l) ? l : `${l}.`);
  }
  return out;
}

export function extractWorkflow(name: string, content: string): ExtractedWorkflow {
  const text = content || "";
  const rawLines = text.split(/\n/);
  const all = sentences(text);
  const used = new Set<string>();

  const process = name.trim() || clean(rawLines.find((l) => clean(l).length > 3) || "") || "Untitled process";

  const approvals = take(all, /approv|sign[- ]?off|escalat|must be reviewed by|needs? (?:a )?(?:lead|manager)/i, used, 5);
  const exceptions = take(all, /\bexcept|exception|edge case|does(?: not|n't) apply|special case|\bunless\b|\bvip\b|enterprise/i, used, 5);
  const decision_rules = take(
    all,
    /\b(if|when|over|under|more than|less than|within|after|before|at least|at most|never|always|only)\b.*(\$?\d|days?|hours?|minutes?|%|plan|tier|sev|p[0-3])/i,
    used,
    6,
  );

  // Steps: prefer explicit numbered / bulleted lines, then fall back to
  // imperative-looking sentences.
  const listLines = rawLines
    .filter((l) => /^\s*(?:\d+[.)]|[-*•])\s+/.test(l))
    .map(clean)
    .filter((l) => l.length >= 15 && !used.has(l));
  const pool = listLines.length >= 3 ? listLines : all.filter((l) => !used.has(l));
  const steps: WorkflowStep[] = pool.slice(0, 7).map((l, i) => {
    const [action, ...rest] = l.split(/\s+[—–-]\s+|:\s+(?=[A-Z])/);
    return {
      step: i + 1,
      action: (action || l).trim(),
      owner: ownerFor(l),
      notes: rest.join(" — ").trim(),
    };
  });
  if (steps.length === 0) {
    steps.push({ step: 1, action: `Follow the documented ${process} process`, owner: "Process owner", notes: "" });
  }

  const description =
    all.find((s) => !used.has(s) && s.length <= 200 && !/^(owner|maintained|last|next|effective|approvers)\b/i.test(s)) ||
    `How the team handles ${process.toLowerCase()}.`;
  const trigger =
    all.find((s) => /\b(when|whenever|upon|triggered|requests?|alert|fires|joins|starts)\b/i.test(s) && s.length <= 180) ||
    `A situation that requires the ${process} process.`;

  return {
    process,
    description,
    trigger,
    steps,
    decision_rules,
    approvals,
    exceptions,
    sources: [`manual:${process}`],
  };
}

function stem(word: string): string {
  return word.replace(/(ing|ed|es|s)$/, "");
}

export function tokens(text: string): Set<string> {
  const out = new Set<string>();
  for (const w of (text || "").toLowerCase().match(/[a-z0-9$]+/g) || []) {
    if (w.length < 3 || STOPWORDS.has(w)) continue;
    out.add(stem(w));
  }
  return out;
}

/** Fraction of `query` tokens present in `doc`, mapped onto a 0.2–0.95
 *  band so it reads like a cosine similarity in API responses. */
export function keywordSimilarity(query: string, doc: string): number {
  const q = tokens(query);
  if (q.size === 0) return 0;
  const d = tokens(doc);
  let hit = 0;
  for (const t of q) if (d.has(t)) hit += 1;
  const frac = hit / q.size;
  return Math.min(0.95, 0.2 + 0.75 * frac);
}

export function jaccard(a: string, b: string): number {
  const ta = tokens(a);
  const tb = tokens(b);
  if (ta.size === 0 || tb.size === 0) return 0;
  let inter = 0;
  for (const t of ta) if (tb.has(t)) inter += 1;
  return inter / (ta.size + tb.size - inter);
}
