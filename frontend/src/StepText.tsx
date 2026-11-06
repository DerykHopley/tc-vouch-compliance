import type { Step } from './assessment.ts'

/** An agent step's text: an interface sentence, with the ISA edition's words it quotes (`quote`, the load step's
 * question) marked in the profile's language (#244). */
export default function StepText({ step, language }: { step: Step; language?: string }) {
  const at = step.quote ? step.text.indexOf(step.quote) : -1
  if (!step.quote || at === -1) return <>{step.text}</>
  return (
    <>
      {step.text.slice(0, at)}
      <span lang={language}>{step.quote}</span>
      {step.text.slice(at + step.quote.length)}
    </>
  )
}
