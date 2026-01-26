# Claude Code Configuration

This directory contains configurations and instructions for AI agents working on this project.

## Structure

```
.claude/
├── CLAUDE.md           # Main instructions for AI agents
├── README.md           # This file
├── credentials.md      # Project credentials (not committed)
├── agents/             # Specialized agents
│   ├── code-reviewer.md
│   ├── python-developer.md
│   ├── test-engineer.md
│   └── troubleshooter.md
└── commands/           # Custom commands
    └── code-standards.md
```

## CLAUDE.md

Main document with:
- Project overview
- Code standards
- Project structure
- Development guide
- CI/CD
- Troubleshooting

## Agents

Specialized agents that can be invoked for specific tasks:

- **code-reviewer**: Code review and quality
- **python-developer**: Python development
- **test-engineer**: Tests and coverage
- **troubleshooter**: Debug and problem resolution

## Commands

Custom commands for common tasks:

- **code-standards**: Apply project code standards

## Credentials

The `credentials.md` file contains tokens and credentials needed for:

- **PyPI**: Package publishing
- **GitHub**: Release creation and API operations
- **GitHub Actions**: Secrets for workflows

⚠️ **IMPORTANT**: This file is in `.gitignore` and should never be committed.

### For Maintainers

If you have maintenance access to the project:

1. Read `credentials.md` to see the tokens
2. Configure tokens locally as instructed
3. Keep secure backup of the tokens
4. Never share tokens publicly

## For Contributors

If you're using Claude Code or another AI tool to contribute to this project:

1. Read `CLAUDE.md` first
2. Use specialized agents when appropriate
3. Follow the described code standards
4. Run tests before opening PRs

## Note

These files are specific to AI tools and do not affect the code or library functionality.
