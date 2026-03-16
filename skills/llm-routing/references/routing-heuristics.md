# LLM Routing Heuristics

## Alias Map

- `CX-Executor` = Codex execution lane
- `CC-Diagnostician` = Claude diagnosis and wording lane
- `GI-Mapper` = Gemini mapping and decomposition lane

Recommended default handoff:
- `GI-Mapper -> CC-Diagnostician -> CX-Executor -> CC-Diagnostician`

## Decision Matrix

| Task Category | Subcategory | Recommended | Confidence | Reasoning |
|---------------|-------------|-------------|------------|-----------|
| **Code Generation** | Boilerplate/scaffolding | Codex | High | Pattern-heavy, well-trained on common structures |
| | CRUD endpoints | Codex | High | Repetitive, well-defined patterns |
| | UI components from spec | Codex | High | Visual patterns, component libraries |
| | Complex algorithms | Claude | High | Requires reasoning about correctness |
| | State machines | Claude | Medium | Complex transitions need careful design |
| **Bug Fixing** | Clear repro, single file | Codex | High | Focused, well-scoped changes |
| | Complex multi-file bug | Claude | High | Requires tracing execution across files |
| | Race conditions | Claude | High | Concurrency reasoning |
| | Memory leaks | Claude | Medium | Depends on complexity |
| **Refactoring** | Rename/move | Codex | High | Mechanical, well-defined |
| | Extract function/class | Codex | Medium | Depends on complexity |
| | Architecture restructure | Claude | High | Cross-cutting concerns, trade-offs |
| | Design pattern application | Claude | Medium | Requires judgment |
| **Testing** | Unit test generation | Codex | High | Pattern-heavy, repetitive |
| | Integration tests | Codex | Medium | May need architecture understanding |
| | E2E test scenarios | Claude | Medium | Requires behavioral reasoning |
| | Test debugging | Claude | High | Multi-step reasoning |
| **Security** | Auth implementation | Claude | High | Security-critical, needs careful review |
| | Input validation | Claude | High | Must consider attack vectors |
| | Crypto operations | Claude | High | Correctness critical |
| | CORS/headers setup | Codex | Medium | Well-documented patterns |
| **DevOps** | CI/CD pipeline | Codex | Medium | Template-driven |
| | Docker/container config | Codex | High | Well-documented patterns |
| | Dependency upgrades | Codex | High | Mechanical changes |
| | Infrastructure as code | Codex | Medium | Pattern-heavy |
| **Documentation** | API docs | Claude | High | Explanatory writing |
| | README/guides | Claude | High | Requires understanding of context |
| | Large-context synthesis | Gemini | Medium | Strong at multi-model summarization and long-context condensation |
| | Code comments | Claude | Medium | Contextual understanding needed |
| | JSDoc/docstrings | Codex | Medium | Pattern-heavy |
| **Data** | Schema design | Claude | High | Requires trade-off analysis |
| | Migration scripts | Codex | High | Well-defined transformations |
| | Query optimization | Claude | Medium | Requires understanding of access patterns |
| | Data validation | Claude | Medium | Edge case reasoning |

## Model Selection Guide

### Codex Models
- **gpt-5.3-codex** (default): Best all-around for code generation
- Use for: most `CX-Executor` tasks

### Claude Models
- **sonnet**: Good balance of speed and quality for most tasks
- **opus**: Complex architecture, security-critical code, nuanced debugging
- **haiku**: Lightweight tasks like product verification, simple reviews
- Inspect `claude_runs/manifest.jsonl` for the detected model used by the CLI during `CC-Diagnostician` execution

### Gemini Models
- **gemini-2.5-pro**: Use when you want an explicit Gemini model selection for harder tasks
- **CLI default routing**: May use more than one Gemini model internally; inspect `gemini_runs/manifest.jsonl` for the detected main model and all detected models
- Use for: large-context synthesis, mapping, and `GI-Mapper` decomposition work

## Parallel Execution Opportunities

These task combinations can run simultaneously:
- Multiple Codex implementations on different files
- QA testing while reviewer checks diff
- Product verification while QA runs
- Research agents exploring different aspects of the codebase
