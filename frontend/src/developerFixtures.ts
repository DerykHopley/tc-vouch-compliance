import type { DeveloperPrompts, DeveloperSettings, RunTotals } from './developer.ts'

/** Test data (#418): no assessments run, and a developer's settings with none, shared by the developer API's, the
 * developer page's and ProfiledApp's tests so they agree on one shape. */
export const NO_RUNS = {
  assessments: 0, input_tokens: 0, output_tokens: 0, reported_cost_usd: 0, estimated_cost_usd: 0, incomplete: 0,
} satisfies RunTotals

export const DEVELOPER_SETTINGS = {
  model: 'anthropic/claude-haiku-5.5',
  runs_on: 'openrouter',
  fake_reason: null,
  prompt_version: '1',
  usage_url: 'https://openrouter.ai/activity',
  kept_in: 'database',
  daily_limit: 30,
  usage: { openrouter: NO_RUNS, fake: NO_RUNS },
  broken_profiles: [],
  companies: [],
  listed_companies: [
    { id: 'alpenwerk', company: 'Alpenwerk Präzision GmbH', country: 'Austria', answer_language: 'de' },
    { id: 'nordlicht', company: 'Nordlicht Komponenten GmbH', country: 'Germany', answer_language: 'en' },
  ],
  current_company: 'nordlicht',
  prices: [
    { model: 'anthropic/claude-haiku-5.5', input_per_million: 0.1, output_per_million: 0.5 },
    { model: 'anthropic/claude-sonnet-5.5', input_per_million: 2, output_per_million: 10 },
  ],
  recent_runs: [],
} satisfies DeveloperSettings

/** Test data (#496): the prompts as GET /api/developer/prompts answers them for Nordlicht and 1.1.1, cut short. */
export const DEVELOPER_PROMPTS = {
  company: { id: 'nordlicht', name: 'Nordlicht Komponenten GmbH' },
  controls: [
    { id: '1.1.1', question: 'To what extent are information security policies available?' },
    { id: '1.6.2', question: 'To what extent is the organization prepared to handle crisis situations?' },
  ],
  control: { id: '1.1.1', question: 'To what extent are information security policies available?' },
  prompts: [
    { name: 'gather', title: 'Gather evidence', version: 'm7-lead-statements-1', sent: true,
      note: 'Sent first on every assessment.',
      messages: [
        { role: 'system', text: 'You assess one VDA ISA control for Nordlicht Komponenten GmbH.' },
        { role: 'human', text: "Control 1.1.1: To what extent…\nNote: [from the run: the lead's rejection note]" },
      ] },
    { name: 'overlay', title: 'Country overlay', version: 'm7-lead-statements-1', sent: false,
      note: 'Not sent for this control: it has no overlay requirement.', messages: [] },
    { name: 'judge', title: 'Judge', version: 'm7-lead-statements-1', sent: true, note: 'Sent once gathering ends.',
      messages: [{ role: 'system', text: 'Judge each requirement.' }, { role: 'human', text: 'Evidence: …' }] },
    { name: 'remediation', title: 'Remediation', version: 'm7-lead-statements-1', sent: true,
      note: "Sent after the judge when a requirement isn't fully evidenced.",
      messages: [{ role: 'system', text: 'Draft one remediation item.' }, { role: 'human', text: 'Not fully evidenced:' }] },
    { name: 'profile_pass', title: 'Profile pass', version: 'm7-profile-pass-2', sent: true,
      note: 'Sent once per profile pass.',
      messages: [{ role: 'system', text: 'You assess every control.' }, { role: 'human', text: 'Controls:' }] },
    { name: 'chat', title: 'Chat', version: 'chat-8', sent: true, note: 'Sent with every chat message.',
      messages: [{ role: 'system', text: 'You are Vouch.' }] },
  ],
} satisfies DeveloperPrompts
