# Cocapn Fleet Integration

**The composition boundary for the Cocapn Fleet.**

This meta-repo answers the question: *"Do these versions of components work together?"*

## Quick Start

```bash
# List all pinned components
python3 scripts/resolve-components.py --list

# Clone/update all repos to pinned refs
python3 scripts/resolve-components.py --checkout

# Verify local refs match manifest
python3 scripts/resolve-components.py --verify

# Run contract tests
pytest tests/test_contracts.py -v

# Run the full fleet locally
docker compose up --build
```

## What's in the Manifest

`components.lock` pins 11 fleet components to immutable refs:

| Component | Version | Type | Interfaces |
|-----------|---------|------|------------|
| sunset-ecosystem | 2.1.0 | core | breeding, thermal, decision_journal |
| ccc-os | 1.3.0 | monitor | health, triage, github_discussions |
| cocapn-health | 2.0.1 | health | probe, thermal_snapshot, eventbus |
| cocapn-traps | 1.0.0 | safety | circuit_breaker, trap_framework |
| vector-novelty | 1.2.0 | math | novelty_search, vector_ops |
| pareto-tournament | 1.0.3 | math | pareto_front, tournament_select |
| hebbian-router | 0.9.1 | math | diversity_routing, mesh_gossip |
| cocapn-plato | 1.0.0 | environment | room_api, breeding_env |
| turbovec-integration-ccc | 1.0.0 | compiler | room_grid_compile, hot_swap |
| flux-vm-v3 | 3.0.0 | vm | constraint_check, proof_certificate |
| ai-writings | 1.0.0 | docs | essays, research_briefs |

## Contract Testing

Contract tests validate that each component's public API matches what dependents expect:

- **Provider tests** (in sunset-ecosystem): `swarm.breeder`, `thermal.budget`, `logos.decision_journal`
- **Consumer tests** (in ccc-os, cocapn-health): health report schemas, CLI entrypoints
- **Cross-repo smoke tests**: breeding loop + health monitoring, FLUX gating + compiler

Run them: `pytest tests/test_contracts.py -v`

## Local Fleet Simulation

```bash
# Start the full stack
docker compose up --build

# Services:
# - sunset-ecosystem:8080  — breeding daemon
# - ccc-os:8080            — fleet monitor
# - cocapn-health:8081     — health probes
# - prometheus:9090          — metrics
# - grafana:3000            — dashboards (admin/fleet)
# - nexus:6379              — Redis message bus
# - cocapn-plato:8085       — breeding environment
# - turbovec:8080           — compiler
# - flux-vm:8086            — constraint VM
```

## CI Integration

The `.github/workflows/integration.yml` runs on every push:

1. **Resolve manifest** — validate JSON, list components
2. **Contract tests** — on Python 3.11 + 3.12
3. **Integration smoke** — end-to-end breeding + health checks
4. **Security audit** — bandit + pip-audit across all repos
5. **Coverage gate** — 75% minimum across all Python components

## Updating the Manifest

```bash
# After a component releases a new version:
# 1. Update the ref and version in components.lock
# 2. Run contract tests to verify compatibility
# 3. Commit with message: "Bump sunset-ecosystem 2.1.0 → 2.2.0"
# 4. Tag the integration repo: git tag 2026.06.01-1
```

## Architecture

```
┌─────────────────────────────────────────┐
│         cocapn-fleet-integration          │
│              (this repo)                   │
│  ┌─────────────┐  ┌─────────────────┐    │
│  │ components  │  │  contract tests │    │
│  │    .lock    │  │  (pytest)       │    │
│  └─────────────┘  └─────────────────┘    │
│  ┌─────────────┐  ┌─────────────────┐    │
│  │  docker     │  │  integration CI │  │
│  │ compose.yml │  │  (GitHub Actions)│   │
│  └─────────────┘  └─────────────────┘    │
└─────────────────────────────────────────┘
           │ resolves │
           ▼          ▼
    ┌──────────────────────────┐
    │   11 component repos     │
    │  (each with own CI/CD)   │
    └──────────────────────────┘
```

## License

MIT — Fleet integration belongs to the fleet.
