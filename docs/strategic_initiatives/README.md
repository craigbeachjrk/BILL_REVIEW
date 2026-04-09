# Strategic Initiatives — Bill Review Platform

## Overview
Seven strategic initiatives to transform the Bill Review app from a single-path tool into a production-grade, observable, testable, and portable platform.

## Initiative Index

| ID | Initiative | Status | Priority |
|----|-----------|--------|----------|
| S1 | [Observability & Transaction Visibility](S1_OBSERVABILITY.md) | Phase 1-3 DONE | P0 |
| S2 | [Bill Disappearance Resolution](S2_BILL_DISAPPEARANCE.md) | 9 of 11 fixes done | P0 |
| S3 | [Pipeline Modularity & Chain Testing](S3_PIPELINE_CHAINS.md) | Planning | P1 |
| S4 | [Bill Attribution & User Tracking](S4_BILL_ATTRIBUTION.md) | Phase 1-3 DONE | P1 |
| S5 | [Functional Validation Audit](S5_FUNCTIONAL_AUDIT.md) | Deep dive done | P1 |
| S6 | [Automated Testing & Performance](S6_AUTOMATED_TESTING.md) | Planning | P2 |
| S7 | [Infrastructure as Code (CloudFormation)](S7_CLOUDFORMATION.md) | Planning | P2 |

## How This Works
- Each initiative has its own markdown with objectives, task breakdown, and status tracking
- Memory file at `~/.claude/projects/.../memory/project_strategic_initiatives.md` links to these docs
- On every session, Claude checks and updates task status in these files
- Tasks flow: `[ ] Planning` -> `[~] In Progress` -> `[x] Done`
