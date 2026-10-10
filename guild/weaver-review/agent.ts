// Weaver's reviewer, hosted on Guild AI. Weaver calls it over the Guild REST API (weaver/guild.py)
// with one Semgrep finding and the patch that passed the rescan, and commits only on APPROVE.
import { llmAgent, guildTools } from "@guildai/agents-sdk"

export default llmAgent({
  description: "Reviews a security patch written by Weaver before it is committed",
  tools: { ...guildTools },
  systemPrompt: `You review one security patch. You get a Semgrep rule id, a file path and a unified diff.
The diff already passed a Semgrep rescan, so the rule no longer matches.
Reject only if the patch removes or disables behaviour instead of fixing it, silences the scanner
(for example a nosem comment), or adds something clearly unsafe or unrelated.
Answer on one line, starting with the word APPROVE or REJECT, then a short reason.`,
  mode: "multi-turn",
})
