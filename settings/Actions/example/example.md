---
Name: Example Action
Description: A template action. Replace with real instructions. Loaded on demand by the agent.
AlsoLoad: []
---
# Example Action

This is a placeholder action file. When the agent loads this action, everything below the
frontmatter is injected into its context as detailed instructions for a specific task.

Put step-by-step instructions, conventions, commands, and references here. Keep it focused on
one job the agent should be able to execute reliably.

Anything else placed in this folder (scripts, data, templates) is listed to the agent by path
so it can read those files with the read tool when it loads this action.

Use `AlsoLoad: [other-action-key]` in the frontmatter to pull in dependent actions
automatically (resolved recursively).
