# 222 Strict debater questionnaire

- Version: v2.0
- Status: Approved; completed locally
- Date: 2026-08-24
- Depends on: Specs 201 and 220

## Scope

Replace the current participant annotation wording with the approved debater
questions. New rule-controlled post-match tasks use the same fixed wording and
answer choices. Historical Q1-Q5 records and the legacy optional `q6` storage
field remain readable and are not migrated.

## Fixed questions

The following text, option order and question types are immutable for this
questionnaire version. Display labels may add the listed A-F or 1-5 prefix but
must not rewrite the answer text. Action hints such as locking or multiple
choice behavior must not be appended to the question or option text.

### `HUMAN_SELF`

Q1 is single choice: `当时你为什么选择自己发言，而不是把这个机会留给 AI 或其他队友？请选择最主要的原因。`

- A. `自己更适合处理这个问题。`
- B. `担心AI 不适合或者处理不好这个问题。`
- C. `没人回答或者其他担忧被迫回答`
- D. `其他。`

Q2 is multiple choice: `你这次发言最主要想完成什么？`

- A. `回应对手`：`对手刚提出了需要处理的攻击、质疑或追问`
- B. `接续队友`：`队友的内容需要补充、澄清或继续推进`
- C. `补足缺口`：`我注意到己方有一个重要问题还没人处理`
- D. `主动推进`：`我认为应该推进新的或已有的己方论证`
- E. `调整方向`：`我认为当前讨论需要转向更重要的问题`
- F. `其他`

F has no secondary description.

The Web renders A-E as `letter. option：description`; the full-width colon is
part of the approved visible wording. The stored value remains the option text
only, so this presentation correction does not migrate or invalidate answers.

### `TEAM_AI`

Q1 is single choice and must be saved and locked before revealing the AI
speech: `在发言之前，当时你觉得 AI 这个时候发言合适吗`

- A. `不合适，比如有此时其他人更合适发言：`
- B. `发言不发言都合适：`
- C. `很适合 AI 发言`
- D. `无法判断：`

Q2 is single choice after reveal:
`看完 AI 的实际发言后，你认为这次发言与当时团队需要的匹配程度如何？（不仅看内容）`

- A. `很好，处理了当时团队真正需要处理的问题`
- B. `一般，内容有点冗余，重复表达，新增价值很小`
- C. `一般，有点跑偏或者钻牛角尖，内容可能有价值，但不是当时最需要处理的问题`
- D. `不好，为后续带来了额外的修复负担`
- E. `其他不好或者一般的原因`
- F. `无法判断`

### Overall experience

The section begins with `请根据刚才这场比赛的整体感受，选择最符合你想法的答案。`
It contains exactly the five approved 1-5 questions and their question-specific
option wording from `survey_definitions.py`; it has no open text or Q6 field in
the new-flow UI. The profile-page default uses the same five approved overall
questions. When an older default is still published, the first read archives
that version and creates the new published version; existing responses remain
bound to the old version.

- Q1: `这场比赛里，AI 更像一个会和队友配合的辩手，还是只顾自己对抗的辩手？`
- Q2: `AI 的发言通常有没有接住队友刚才说的内容和场上的情况？`
- Q3: `AI 的发言对我们团队有多大帮助？`
- Q4: `这场比赛里，AI 有没有给你带来额外负担？比如它说得不清楚、有漏洞、和队友重复，你还要花力气去理解、补充或修正。`
- Q5: `如果下一场还要和这个 AI 一起辩，你愿意继续把它当作队友吗？`

The exact five answer scales are covered by the definition regression test;
each rendered option includes its numeric value and original option text.

New post-match tasks are stamped `postmatch-v2-strict-2026-08-24`; submissions
must use the answer shape for this version and must not contain unknown keys.

## Acceptance

- Core rejects unknown choices, array answers for AI matching, and overall
  values outside 1-5.
- New strict experiment tasks reject a non-empty legacy `q6`; the nullable
  database column remains only for historical read compatibility.
- Core does not include AI speech text in the task response until the
  pre-speech answer has been saved.
- A saved AI pre-speech answer is immutable; refreshing the page keeps the
  speech revealed without requiring the post-speech answer.
- Web shows the exact approved text and no old seven-point rating, tag, or
  open-text controls.
- Existing experiment Q1-Q5 API and stored rows remain compatible.

## Rollback

Disable creation of the v2 task or revert source/UI only. Do not alter or
delete historical questionnaire rows.

## Verification

- Core non-integration suite: 335 passed, 33 deselected.
- Web Vitest suite: 186 passed.
- Storybook suite: 89 passed.
- Ruff, Web ESLint, TypeScript, OpenAPI contract and `git diff --check` passed.
- The PostgreSQL integration assertion for rejecting legacy `q6` was added but
  was not run in this workspace because Docker CLI and `TEST_DATABASE_URL` are
  unavailable.
- Storybook browser inspection at 1280 px and 390 px showed 25 overall radio
  options, no Q6 or textarea, and no horizontal overflow.
- Browser verification at 1280 px and 390 px found no horizontal overflow;
  AI speech remained hidden until its pre-speech answer was saved. Axe reported
  no confirmed WCAG A/AA violations.
- The final wording audit renders the HUMAN_SELF Q2 choices as
  `letter. option：description` in both participant entry points. Core now
  requires a valid approved Q1 choice, rather than mere key presence, before
  revealing AI speech or accepting its post-speech Q2. Follow-up verification:
  Core survey tests 8/8, Web tests 186/186, Ruff, ESLint, TypeScript and OpenAPI
  contract checks passed.
- Storybook now includes a dedicated HUMAN_SELF confirmation scenario; the
  Storybook suite passes 90/90 with the exact Q1, Q2 and full-width-colon
  rendering asserted.
- Final API-boundary review rejects `null` as a completed HUMAN_SELF or TEAM_AI
  answer, so direct requests cannot bypass required questions. Personal survey
  drafts may contain a valid subset of the five answers; submit still requires
  all five. Survey/post-match/experiment tests pass 17/17 with one independent
  PostgreSQL case skipped; Ruff and service-module Pyright pass.
