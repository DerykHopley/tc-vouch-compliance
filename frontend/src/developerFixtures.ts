import type { DeveloperSettings, RunTotals } from './developer.ts'

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
} satisfies DeveloperSettings
