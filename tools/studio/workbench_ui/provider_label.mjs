// Display only the explicitly recorded Provider; never infer it from a model.
export const providerLabel=id=>({codex:'Codex',claude:'Claude Code',copilot:'GitHub Copilot'})[id]||'Agent（未确认）';
