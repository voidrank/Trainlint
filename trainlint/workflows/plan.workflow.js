export const meta = {
  name: 'trainlint-plan',
  description: 'Build the project plan deterministically: gather full context, decompose into decisions, and WRITE plan.<name>.jsonl (+ fill the facts files). The write is guaranteed by the script, not left to the model to remember.',
  phases: [
    { title: 'Gather', detail: 'parallel readers over code / configs / memory / runs' },
    { title: 'Decompose', detail: 'synthesize complete context + the ordered decisions' },
    { title: 'Write', detail: 'persist plan.<name>.jsonl + project/research facts' },
  ],
}

// args: { project: "<name>", pluginRoot: "/abs/path/to/plugin/root" }
const project = (args && args.project) || 'project'
const root = (args && args.pluginRoot) || '.'
const planFile = `${root}/research/plan.${project}.jsonl`
const actionFile = `${root}/project.${project}.json`
const researchFile = `${root}/research/facts.${project}.json`

const NOTES_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    slice: { type: 'string' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        properties: {
          fact: { type: 'string' },
          file_line: { type: 'string', description: 'file:line citation, or "" if not code-grounded' },
          plain: { type: 'string', description: 'one plain-language sentence; define any term' },
        },
        required: ['fact'],
      },
    },
    unknowns: { type: 'array', items: { type: 'string' } },
  },
  required: ['slice', 'findings'],
}

const DECISION = {
  type: 'object',
  additionalProperties: false,
  properties: {
    id: { type: 'string' },
    phase: { type: 'string' },
    decision: { type: 'string' },
    choice: { type: 'string' },
    principle: { type: 'string' },
    why: { type: 'string' },
    status: { type: 'string', enum: ['open', 'decided', 'verified'] },
    match: { type: 'string' },
  },
  required: ['id', 'phase', 'decision', 'principle', 'status'],
}

const PLAN_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    exposition: { type: 'string', description: 'COMPLETE start-to-finish context, plain language, every term defined, file:line grounded, UNKNOWNs marked' },
    decisions: { type: 'array', items: DECISION },
    action_facts: { type: 'object', description: "project.<name>.json danger-pattern facts learned from the code (bad_storage_re, locked_configs_re, preproc_trap_re, frozen_component, ...); {} if none determinable" },
    research_facts: { type: 'object', description: 'facts.<name>.json: runs_glob, direction_regex, candidate_moves, trunk_checks; {} if none determinable' },
  },
  required: ['exposition', 'decisions'],
}

// ---- Phase 1: gather — parallel readers, one slice each, grounded + plain-language ----
phase('Gather')
const SLICES = [
  'data pipeline + storage (where data lives, how it is read, train/val split)',
  'preprocessing + the frozen codec/tokenizer and its EXACT training config (sample rate, power, n_codebooks, ...)',
  'model architecture: what is FROZEN vs trained, and the forward / mask / loss',
  'training setup: parallelism, batch, LR, schedule, checkpoint init',
  'eval protocol + deployment / streaming',
]
const notes = await parallel(SLICES.map((s, i) => () =>
  agent(
    `You are reading the training project '${project}'. Plugin root is ${root}. ` +
    `First read ${root}/research/goal.${project}.txt, ${actionFile}, ${researchFile}, and any relevant entries under ` +
    `the user's memory (~/.claude/projects/*/memory). Follow their pointers to the ACTUAL project code/configs and read them. ` +
    `Focus ONLY on this slice: ${s}. ` +
    `Return grounded findings: each with a file:line citation where possible and a one-sentence plain-language explanation that DEFINES any jargon (assume the reader is smart but new to this project). ` +
    `List anything you cannot determine as an explicit UNKNOWN. Do not guess.`,
    { label: `gather:${i + 1}`, phase: 'Gather', schema: NOTES_SCHEMA }
  )
)).then(r => r.filter(Boolean))

// ---- Phase 2: decompose — one synthesizer turns notes into context + decisions + facts ----
phase('Decompose')
const plan = await agent(
  `Here are grounded notes on the training project '${project}', sliced by area:\n\n${JSON.stringify(notes, null, 1)}\n\n` +
  `Produce, for this project:\n` +
  `1. exposition: a COMPLETE, start-to-finish context write-up — the whole pipeline in order (data -> preprocessing -> codec/tokenizer -> packing -> model[frozen vs trained] -> forward -> loss -> training -> eval -> deploy). Plain language, EVERY term defined the first time it appears, every structural claim carrying its file:line, and every gap marked UNKNOWN. No hand-waving.\n` +
  `2. decisions: decompose it into the ordered DECISIONS that define the project — every place a silent choice determines correctness. For each: id (kebab), phase, decision (the question), choice ("" + status "open" if genuinely undecided — never invent one), principle (the transferable governing-law id; reuse one from ${root}/quiz.jsonl if it fits, else coin a kebab id), why (one line), status (open|decided|verified), match (a regex that recognizes an action touching this decision).\n` +
  `3. action_facts: the doorman's danger-pattern facts you learned (the keys in ${root}/project.mimo.json: bad_storage_re, locked_configs_re, preproc_trap_re, preproc_ok_re, frozen_component, reference_impl, and the *_example fields). Leave a key out if you genuinely cannot determine it — never a fake value. {} if none.\n` +
  `4. research_facts: runs_glob, direction_regex, candidate_moves, trunk_checks (see ${root}/research/facts.mimo.json). {} if none.`,
  { label: 'decompose', schema: PLAN_SCHEMA }
)

// ---- Phase 3: write — persist the artifacts (THE guaranteed step that used to get dropped) ----
phase('Write')
const planHeader =
  `# Project PLAN for ${project} — the ordered DECISIONS that define this run, each tagged with the\n` +
  `# transferable PRINCIPLE that governs it. Written by the trainlint-plan workflow.\n` +
  `# fields: id | phase | decision | choice | principle | why | status(open|decided|verified) | match(regex)`
const planBody = (plan.decisions || []).map(d => JSON.stringify(d)).join('\n')

await agent(
  `Use the Write tool to persist these files EXACTLY (create parent dirs if needed). Confirm each write.\n\n` +
  `FILE 1 — ${planFile}\n-----\n${planHeader}\n${planBody}\n-----\n\n` +
  (plan.action_facts && Object.keys(plan.action_facts).length
    ? `FILE 2 — ${actionFile} (merge into the existing file; keep its _comment, add the learned keys):\n${JSON.stringify(plan.action_facts, null, 2)}\n\n`
    : `(skip ${actionFile} — no action facts determined)\n\n`) +
  (plan.research_facts && Object.keys(plan.research_facts).length
    ? `FILE 3 — ${researchFile} (merge into the existing file; keep thresholds):\n${JSON.stringify(plan.research_facts, null, 2)}\n`
    : `(skip ${researchFile} — no research facts determined)\n`),
  { label: 'write', phase: 'Write' }
)

log(`plan written: ${(plan.decisions || []).length} decisions -> ${planFile}`)
return {
  project,
  planFile,
  decisions: (plan.decisions || []).length,
  exposition: plan.exposition,
  decisionList: (plan.decisions || []).map(d => ({ id: d.id, phase: d.phase, decision: d.decision, status: d.status })),
}
