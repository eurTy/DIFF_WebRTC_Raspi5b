# Project Guidance

This repository is the independent research project for "Semantic-adaptive video transmission in constrained environments." Read [PROJECT_BRIEF.md](PROJECT_BRIEF.md) and the relevant experiment README before changing code, deployment, tests, or thesis claims. `PROJECT_BRIEF.md` is a draft for user review: its proposed research direction and open decisions are not established results.

- Keep at most three active video webpages on the Pi, on fixed TCP ports: 8089 (v0.4 baseline), 8090 (working mobile v0.5), and 8091 (v0.6 experiment). Do not add a fourth webpage port, reuse one of these for a different service, or restart retired 8080/8088 while all three are active. Check actual listeners before and after deployment. The local-only 8092 diagnostics API is not a video webpage.
- Preserve the existing camera owner and the working 8089/8090 services. New media experiments reuse the 8091 entry and read-only camera input; inspect live state and the dirty worktree before edits.
- Distinguish local source, deployed images, archived working versions, desktop synthetic tests, Pi loopback/TURN tests, and real iPhone 4G observations.
- Do not infer optical end-to-end latency or mobile performance from software timestamps, desktop browsers, container health, or same-host RTP tests.
- Keep camera frames, passwords, TURN credentials, tailnet state, image archives, and private logs out of Git. Save only minimal numeric evidence for live-input experiments.
- For a changed transport or adaptation policy, record a falsifiable hypothesis, a baseline, matched conditions, success metrics, negative results, and the rollback path.
- Thesis text must separate implemented behavior, experimentally supported findings, planned work, and known limitations.
