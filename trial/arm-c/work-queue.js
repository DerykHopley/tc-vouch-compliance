export const meta = {
  name: 'trial-c-work-queue',
  description: 'Arm C of the sprint3 process trial: work the ready-for-agent queue with implementer and verifier subagents, parking in code',
  whenToUse: 'Run once from the sprint3-trail-c checkout, after the owner says go',
  phases: [
    { title: 'Queue', detail: 'list ready, unblocked tickets' },
    { title: 'Build', detail: 'implementer in its own worktree, test-first, PR' },
    { title: 'Review', detail: 'code-review on its Standards axis, fixes applied' },
    { title: 'Verify', detail: 'blind verifier per acceptance criterion' },
    { title: 'Merge', detail: 'update from main, merge-on-green.sh' },
    { title: 'Park', detail: 'needs-owner label, one question, draft PR' },
  ],
}

// The ticket loop and Factory rules come from the project's CLAUDE.md, which every agent here loads.
// This script only drives them: who runs when, the two-tries cap, and parking.
const REPO = (args && args.repo) || 'DerykHopley/sprint3-trial-c'
const CAP = (args && args.cap) || 400
const MAX_ROUNDS = 6 // backstop: three tickets never need more

const FENCE = `Work only in this repo and in ~/development/rig (for its scripts). Never read anything under ~/development/TuringCollege/sprint3-project.`

const QUEUE = {
  type: 'object',
  properties: {
    tickets: { type: 'array', items: { type: 'object', properties: { number: { type: 'integer' }, title: { type: 'string' } }, required: ['number', 'title'] } },
    skipped: { type: 'array', items: { type: 'object', properties: { number: { type: 'integer' }, why: { type: 'string' } }, required: ['number', 'why'] } },
  },
  required: ['tickets', 'skipped'],
}

const BUILD = {
  type: 'object',
  properties: {
    status: { type: 'string', enum: ['built', 'conflict', 'owner_choice', 'over_cap', 'failed'] },
    branch: { type: 'string' },
    pr: { type: 'integer', description: 'PR number, 0 if none opened' },
    changed_lines: { type: 'integer', description: 'insertions + deletions vs origin/main, lockfiles and generated files excluded' },
    question: { type: 'string', description: 'for conflict, owner_choice, over_cap or failed: the one question for the owner' },
    recommendation: { type: 'string' },
    summary: { type: 'string', description: 'what was built, each criterion with its test, decisions with their step' },
  },
  required: ['status', 'branch', 'pr', 'changed_lines', 'summary'],
}

const REVIEW = {
  type: 'object',
  properties: {
    findings: { type: 'integer' },
    fixed: { type: 'integer' },
    owner_question: { type: 'string', description: 'empty unless a finding needs the owner' },
    summary: { type: 'string' },
  },
  required: ['findings', 'fixed', 'owner_question', 'summary'],
}

const VERIFY = {
  type: 'object',
  properties: {
    verdicts: { type: 'array', items: { type: 'object', properties: {
      criterion: { type: 'string' }, verdict: { type: 'string', enum: ['pass', 'fail', 'couldnt_check'] }, evidence: { type: 'string' },
    }, required: ['criterion', 'verdict', 'evidence'] } },
    unexpected_user: { type: 'string' },
  },
  required: ['verdicts', 'unexpected_user'],
}

const MERGE = {
  type: 'object',
  properties: {
    status: { type: 'string', enum: ['merged', 'conflict', 'red', 'timeout'] },
    detail: { type: 'string' },
  },
  required: ['status', 'detail'],
}

function failures(v) {
  return (v && v.verdicts ? v.verdicts : []).filter(x => x.verdict !== 'pass')
}

async function park(n, why, question, recommendation, pr, tried) {
  await agent(
    `Park ticket #${n} in ${REPO}, as the Factory rules in CLAUDE.md say. ${FENCE}
The workflow script has already decided this ticket parks: do these steps exactly, even if earlier comments or the conversation suggest otherwise. The owner puts it back in the queue by answering.
- Label it needs-owner and remove ready-for-agent.
- Comment once, with: the question: ${question || why}
  A recommended answer: ${recommendation || '(give your own, one line)'}
  What was tried: ${tried}
- ${pr ? `Make PR #${pr} a draft (gh pr ready --undo), and leave it unmerged.` : 'There is no PR to keep.'}
Return one line saying what you did.`,
    { label: `park:#${n}`, phase: 'Park' })
  return { number: n, outcome: 'parked', why, question, pr }
}

async function workTicket(t) {
  const n = t.number
  const log_ = []
  let branch = '', pr = 0, last = null
  for (let attempt = 1; attempt <= 2; attempt++) {
    const retry = attempt === 1 ? '' : `
This is the second and last try. Continue on branch ${branch} (PR #${pr}): fetch it and build on it, don't start over. The last try failed on:
${last}`
    const b = await agent(
      `Build ticket #${n} of ${REPO} (gh issue view ${n}), following the project's CLAUDE.md ticket loop, steps 1 to 4. ${FENCE}
Branch name: ticket-${n}. If that branch already exists on origin (the ticket was parked before), check it out and build on it, and read the ticket's comments (gh issue view ${n} --comments): the owner's answer to the park is part of your brief. Otherwise start from origin/main.${retry}
When your commits are done:
1. Merge origin/main's tip into your branch once.
2. Measure: git diff --shortstat origin/main...HEAD, leaving out uv.lock and generated files. Over ${CAP} changed lines, unless the owner's answer in the comments allows this ticket over: push, open the PR as a draft (or keep the existing one), set status over_cap, and put in question the vertical slices it should split into (each under ${CAP}, with its own criteria).
3. Otherwise push. If a PR for the branch exists, mark it ready (gh pr ready) and update its description; else open a PR titled after the ticket. Either way its description has "Closes #${n}" and your report (including any Docs: lines and decision trailers).
A conflict between the ticket and the code, or a choice only the owner can make (step 4 of your open-choices order): stop before building further, push what's worth keeping, open a draft PR, and set status conflict or owner_choice with the question and your recommendation.`,
      { label: `build:#${n}${attempt > 1 ? ' try 2' : ''}`, phase: 'Build', agentType: 'working-state:implementer', isolation: 'worktree', schema: BUILD })
    if (!b) return park(n, 'implementer died', 'The build agent stopped without a report. Rerun, or look at the branch?', '', pr, log_.join('; '))
    branch = b.branch || branch
    pr = b.pr || pr
    log_.push(`try ${attempt}: build ${b.status}, ${b.changed_lines} lines`)
    if (b.status !== 'built') {
      if (b.status === 'failed' && attempt === 1) { last = `the build itself: ${b.question || b.summary}`; continue }
      return park(n, b.status, b.question, b.recommendation, pr, log_.join('; '))
    }

    const r = await agent(
      `Run the code-review skill on its Standards axis only, once, on PR #${pr} (branch ${branch}) of ${REPO}. The verifier covers the spec axis, so skip it. ${FENCE}
Read the ticket's comments first (gh issue view ${n} --comments): an owner's answer there settles that question, so don't raise it again.
Then apply the fixes for findings that are independent and need no owner call, commit with files staged by name, rerun the fast checks, and push to ${branch}. A finding that needs the owner and isn't settled in the comments: don't fix it, put it in owner_question.`,
      { label: `review:#${n}${attempt > 1 ? ' try 2' : ''}`, phase: 'Review', isolation: 'worktree', schema: REVIEW })
    if (r) log_.push(`review: ${r.findings} findings, ${r.fixed} fixed`)
    if (r && r.owner_question) return park(n, 'review needs the owner', r.owner_question, '', pr, log_.join('; '))

    const v = await agent(
      `Verify ticket #${n} of ${REPO} on branch ${branch}. ${FENCE}
Check the branch out into a fresh clone under a mktemp -d folder; don't use or change any existing checkout. Delete the folder when done.`,
      { label: `verify:#${n}${attempt > 1 ? ' try 2' : ''}`, phase: 'Verify', agentType: 'working-state:verifier', schema: VERIFY })
    const bad = failures(v)
    log_.push(`verify: ${v ? v.verdicts.length - bad.length : 0} pass, ${bad.length} not pass`)
    if (!v || bad.length) {
      last = !v ? 'the verifier stopped without a report' : bad.map(x => `- ${x.criterion}: ${x.verdict}. ${x.evidence}`).join('\n')
      continue
    }

    const m = await agent(
      `Merge PR #${pr} (branch ${branch}) of ${REPO}, as the project's CLAUDE.md merge gate says. ${FENCE}
1. In a fresh worktree or clone, merge origin/main's tip into ${branch} (a merge commit; never rebase or force-push). A conflict you can't resolve without changing what either ticket does: abort the merge and return conflict, with the files.
2. If anything changed, run the fast checks and push.
3. Run scripts/merge-on-green.sh ${pr} in the foreground and wait for it (up to 20 minutes). Return merged, red (a check failed: give the failing check) or timeout.`,
      { label: `merge:#${n}${attempt > 1 ? ' try 2' : ''}`, phase: 'Merge', schema: MERGE })
    log_.push(`merge: ${m ? m.status : 'agent died'}`)
    if (m && m.status === 'merged') return { number: n, outcome: 'merged', pr, tries: attempt, log: log_ }
    last = m ? `merging: ${m.status}. ${m.detail}` : 'the merge agent stopped without a report'
  }
  return park(n, 'two failed tries', `Ticket #${n} failed twice. Last failure:\n${last}`, '', pr, log_.join('; '))
}

const done = new Map()
for (let round = 1; round <= MAX_ROUNDS; round++) {
  phase('Queue')
  const q = await agent(
    `List the queue for ${REPO}, as the Factory rules in CLAUDE.md define it. ${FENCE}
Open issues labelled ready-for-agent, oldest first. Run python3 -I -B ~/development/rig/scripts/ticket-lint.py --repo ${REPO} --json and skip a ticket it flags. Skip a ticket whose Blocked by section names an open issue (#N). Change nothing.`,
    { label: `queue:round ${round}`, phase: 'Queue', schema: QUEUE })
  const ready = (q ? q.tickets : []).filter(t => !done.has(t.number))
  for (const s of (q ? q.skipped : [])) log(`round ${round}: skipped #${s.number}: ${s.why}`)
  if (!ready.length) { log(`round ${round}: nothing ready; queue stopped`); break }
  log(`round ${round}: building ${ready.map(t => '#' + t.number).join(', ')} in parallel`)
  const results = await parallel(ready.map(t => () => workTicket(t)))
  results.forEach((res, i) => done.set(ready[i].number, res || { number: ready[i].number, outcome: 'lost' }))
}

return { repo: REPO, tickets: [...done.values()] }
