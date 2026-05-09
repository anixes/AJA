import { Command } from 'commander';

const BASH_COMPLETION = `
_agentx_completion() {
  local cur prev words cword
  _init_completion || return

  local commands="ask review code fix explain map trace impact chat help completion"
  
  case "\${prev}" in
    --mode)
      COMPREPLY=( $(compgen -W "read-only suggest-only ask-before-edit auto-edit-safe autonomous-branch" -- "$cur") )
      return
      ;;
    --provider)
      COMPREPLY=( $(compgen -W "openai anthropic gemini ollama vllm" -- "$cur") )
      return
      ;;
  esac

  if [[ "\${cur}" == -* ]]; then
    COMPREPLY=( $(compgen -W "--version --mode --model --provider --stream --cost --verbose --help" -- "$cur") )
    return
  fi

  if [[ "\${cword}" -eq 1 ]]; then
    COMPREPLY=( $(compgen -W "\${commands}" -- "$cur") )
    return
  fi
}
complete -F _agentx_completion agentx
`;

const ZSH_COMPLETION = `
#compdef agentx

_agentx() {
  local -a commands
  commands=(
    'ask:Single-shot query'
    'review:Review code changes'
    'code:Agentic coding loop'
    'fix:Diagnose and fix errors'
    'explain:Explain a module or function'
    'map:Index repo into knowledge graph'
    'trace:Trace connections between symbols'
    'impact:Blast radius analysis'
    'chat:Launch interactive TUI chat mode'
    'help:display help for command'
    'completion:Generate shell completion script'
  )

  _arguments \\
    '--version[output the version number]' \\
    '--mode[Execution mode]:mode:(read-only suggest-only ask-before-edit auto-edit-safe autonomous-branch)' \\
    '--model[Model override]:model: ' \\
    '--provider[Provider override]:provider:(openai anthropic gemini ollama vllm)' \\
    '--stream[Enable streaming output]' \\
    '--cost[Show cost summary after execution]' \\
    '--verbose[Verbose output]' \\
    '--help[display help for command]' \\
    '1: :->cmds' \\
    '*::arg:->args'

  case "$state" in
    cmds)
      _describe 'commands' commands
      ;;
  esac
}

compdef _agentx agentx
`;

export function getCompletionScript(shell: string): string {
  if (shell === 'zsh') return ZSH_COMPLETION;
  return BASH_COMPLETION;
}
