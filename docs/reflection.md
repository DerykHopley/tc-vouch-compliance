# Reflection

Where Vouch falls short, and what would come next. The reasons behind the design are in [technical decisions](technical-decisions.md); the measured quality is in the [eval summary](eval-summary.md).

## Potential problems

- **Country rules and overlays are illustrative, not researched.** Alpenwerk's Austrian NIS2 overlay (an early warning to the national CSIRT within 24 hours) stands in for a real national law so the graph's overlay branch has a real case. It isn't legal advice, and no country's rules were checked ([decision of 2026-11-02](requirements.md#decisions)).
- **All evidence is invented fixture data.** Nordlicht Komponenten GmbH and Alpenwerk Präzision GmbH, their policies, records and Patch responses are made up ([`fixtures/`](../fixtures/nordlicht/profile.yaml)). The deployed app holds no real documents. Real evidence is messier: scans, spreadsheets, wikis, many versions of one policy.
- **The database stops with the free trial.** Cloud SQL runs on the owner's Google Cloud free trial credits; when the trial ends the instance stops unless the account is upgraded, and Cloud Run with it ([decision of 2026-11-04](requirements.md#decisions)).
- **The model makes mistakes.** The best model in the pipeline, Sonnet 5.5, gets 76% of Nordlicht's verdicts and 9 of 12 controls right; Haiku 5.5 51%; the local gpt-oss 20B 8% ([eval summary](eval-summary.md#headline-figures)). That is why every verdict is a proposal the compliance lead reviews, and why only accepted verdicts count towards readiness.
- **Sonnet's judge step sometimes skips requirements.** In the eval it left 29 requirements unjudged across five assessments, which Vouch shows as not-evidenced: an answer can call a requirement evidenced while its row says otherwise ([eval report](eval-report.md#sonnets-unjudged-requirements); Fix #184).
- **The pipeline loses more than the model does.** With all the evidence in front of it, Haiku scores 80% and Sonnet 90%; inside Vouch's search-and-judge graph, 51% and 76%. The keyword search and the call budget of 12 model calls limit what the agent finds ([eval report](eval-report.md#outside-the-pipeline-the-models-with-all-the-evidence-in-front-of-them)).
- **Alpenwerk's expected answers weren't checked.** An agent drafted them and the owner approved them as drafted after reviewing 5 of 100, so Alpenwerk's scores measure agreement with a draft.
- **Every signed-in user sees every profile.** Both companies are invented, so any signed-in user may switch between them; a real customer's evidence would be open to every user. Per-user profile assignment must come before real data ([decision of 2026-11-05](requirements.md#decisions)).
- **Phone numbers aren't redacted.** A pattern for them removed ordinary text such as date ranges and document numbers, so it was dropped until phone numbers show up in evidence ([decision of 2026-11-05](requirements.md#decisions), #151).
- **The Jev Router's choices are unknown for its kept run.** The Jev Router's eval run predates the recording of which model served each call, so where those answers went isn't known. A compliance tool should be able to say where a company's evidence goes.

## Improvements, and why each isn't in v1

- **Maturity levels.** Verdicts say how far a control is evidenced, not how mature the process is. Not in v1: maturity needs process judgement documents rarely show, and a guessed "level 3" would claim more than was evidenced ([decision of 2026-11-03, "no maturity score"](requirements.md#decisions)).
- **Upload.** Evidence came from a folder per profile, indexed by one command; upload was left out of v1 as a security surface (file type, size, path, injection). It came later as Add document (#585, #586), with limits on the file and a warning on instruction-like text. Evidence is shared per company while assessments are per user, so one user could steer another's verdicts (#623, S1): since #624 only the company's creator or a developer may add, version or withdraw a document, and each change is recorded in the audit trail of every lead with work under the company.
- **Interface translation.** The interface is English; answers, the chat and the readiness report follow the profile's language. Not in v1: translating menus shows no agent behaviour and costs a day.
- **Filling ISA's own Excel.** Auditors work from the VDA ISA workbook; Vouch could fill it in. Not in v1: the readiness report page serves the same readers first, and a filled-in workbook is a modified copy, which ISA's licence allows only marked as not approved by ENX.
- **A generated PDF, and sharing the report by link or e-mail.** The readiness report prints to PDF from the browser and exports Markdown (#56, #57). Not in v1: printing covers the need without a PDF library, and sharing by link needs access rules for people outside Vouch.
- **Asset and risk management.** Real audits start from risk ("what if a laptop is stolen"). The profile has room for an asset register and risks, unused. Not in v1: it needs its own design round (asset types, risk scale, how risk reorders the prep), and was the first stretch item ([decision of 2026-11-03, "asset and risk management"](requirements.md#decisions)).
- **The Lithuanian profile.** A third company, for reviewers who read Lithuanian. Not in v1: a nice-to-have after the German profile, which already shows a second language and an overlay.
- **Log excerpts and live Patch as evidence sources.** v1 searches policies and records, and reads a recorded Patch response. Not in v1: log excerpts were worth it only with more logging controls in the subset, and live Patch means a network dependency with a slow wake-up; both are stretch items behind one evidence-source interface.
- **Langfuse** for observability of each run's model calls. Not in v1: a stretch item after the must list; each run's steps, guard steps and usage are already shown and kept.
- **Local or EU-hosted models, for confidentiality.** Evidence goes to a hosted model outside the company, through OpenRouter, today, which is fine for invented data and not for real. A real deployment needs the provider's data-retention terms read and agreed, a local or EU-hosted model, and a classification check before anything leaves the company. Not in v1: the data is invented, and the eval shows the local model tried, gpt-oss 20B, can't drive the graph yet (8% of verdicts); the EU-hosted candidate, Mistral, was blocked by the owner's OpenRouter account setting ([eval report](eval-report.md#the-local-model-what-confidentiality-costs); [decision of 2026-11-03, "invented data only"](requirements.md#decisions)).

## From prototype to business

The right design depends on the customer's size. A small business is well served by Vouch as it is: one compliance lead, a folder of policies and records, a handful of assessments a day. A large business needs the same agent behind different plumbing. Most of the change sits around the graphs, not in them.

| | Small business (Vouch today) | Large business |
|---|---|---|
| Evidence sources | One folder of policies and one of records per profile, plus a recorded Patch response ([decision of 2026-11-03, "a folder per profile"](requirements.md#decisions)) | SharePoint or Confluence, Jira, the CMDB, the SIEM, MDM and a live Patch, each reached through MCP or its API |
| Tool calls | Local and fast, so a reply's calls run one after another | Network calls, run concurrently, each with its own timeout and retries |
| Search | Keyword search reads the folder's files on each request | A maintained index or vector store, kept up to date as documents change |
| Assessing | One control at a time while the lead watches, or one profile pass with all the evidence in one prompt | A queue of background jobs, with progress, resume after a failure, and a report when done |
| Failure | A source that can't be read is named and the control's run goes on; Patch past its wait marks the evidence pending; the profile pass refuses outright | Partial answers by design, each naming the missing source, and a later run filling the gap |
| Limits | A per-user daily limit on assessments, a request rate limit, and the spending cap on the OpenRouter key ([decision of 2026-11-03, "spending guard"](requirements.md#decisions)) | Per-vendor rate limits, a budget per customer, and cached answers for unchanged evidence |
| Tenancy | One profile per company; every signed-in user sees every profile, as the data is invented ([decision of 2026-11-05](requirements.md#decisions)) | Isolated customers, each with their own data, keys, model provider and hosting region |

**The tool-call row, in the code** (checked at `e8bfac3`). Both graphs run a reply's tool calls one after another through Vouch's own tools node, not LangGraph's prebuilt one:
- the assessment graph's `tools` node in `vouch.graph` loops over the calls;
- the chat's `_tools` node in `vouch.chat_agent` calls each tool in turn, then redacts each reply.

Each call passes the profile's allow-list (`vouch.allow_list`), the folder guards and redaction on the way, so the node is where the security layers meet. Running the calls concurrently would be a contained change in those two nodes, for example `asyncio.gather` over the calls with a timeout each, not a new graph. The guards would stay per call. For a small business it buys nothing: the tools read local files in milliseconds, and the model call around them takes seconds.
