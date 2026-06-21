# CI quality and security gates

This repo has two GitHub Actions workflows:

- `tests.yml` is the functional suite: it runs the unit tests across Python
  3.10, 3.11, and 3.12 and runs the pre-release smoke checks.
- `ci-quality-gates.yml` adds the code-quality and security gates described
  here: test with coverage, then a SonarCloud quality gate and a Snyk
  dependency scan, then a gated build-artifact stage. It mirrors the pipeline
  shipped in the sibling adsb-to-wdgwars (Muninn) and wigle-to-wdgwars repos.

## The gated pipeline

`ci-quality-gates.yml` runs on every push and pull request to `main`, and on
manual dispatch. It is structured as gated stages:

1. **Test and coverage.** Installs dev dependencies (Heimdall itself has no
   runtime deps), runs the suite under pytest with `pytest-cov`, and produces
   `coverage.xml`. The coverage report is uploaded as a build artifact.
2. **SonarCloud quality gate.** Downloads `coverage.xml` and runs the
   SonarCloud scanner. `sonar.qualitygate.wait=true` makes the scanner block
   on the gate, so a failed gate fails the job rather than only showing on the
   dashboard.
3. **Snyk dependency scan.** Runs Snyk software composition analysis on
   `requirements.txt`. Heimdall is pure stdlib, so this is effectively a no-op
   today; it is kept for family parity and to catch the day a runtime
   dependency is pinned.
4. **Build release artifact.** Stages a bundle of `heimdall.py` and its install
   files. This stage declares `needs: [sonarcloud, snyk]`, so it only runs
   after both gates pass.

## Coverage gate

The local gate is a regression floor in `pyproject.toml`
(`[tool.coverage.report] fail_under`), set just below the current measured
baseline of about 20 percent line and branch coverage on `heimdall.py`. The
build fails if coverage drops below the floor. Raise the floor as tests are
added; it is a ratchet, not a target.

The SonarCloud gate is the forward-looking quality enforcement. The default
Sonar way gate judges new code on each branch or pull request: new-code
coverage, no new bugs, vulnerabilities, or code smells, and security hotspots
reviewed. Adding tests and clean code keeps it green; introducing untested or
problematic new code fails it.

## One-time setup (free tiers)

Both services are free for public repositories. Until these secrets exist the
`sonarcloud` and `snyk` jobs fail (the `test` stage and coverage floor are
independent and pass on their own).

### SonarCloud

1. Sign in at https://sonarcloud.io (EU region) with the GitHub account and
   import this repo. Confirm the organization and project keys match
   `sonar-project.properties`. (The US region lives at https://sonarqube.us;
   the account is tied to whichever instance you sign up on.)
2. In the project settings, turn off Automatic Analysis so the CI scanner is
   the source of truth. (If Automatic Analysis is left on, the CI-based scan
   refuses to run — the two cannot both analyse the project.)
3. Create a token under My Account, Security, and add it to this repo as the
   `SONAR_TOKEN` Actions secret (Settings, Secrets and variables, Actions).

### Snyk

1. Sign up at https://snyk.io with the GitHub account (free Open Source plan).
2. Copy the API token from Account settings and add it to this repo as the
   `SNYK_TOKEN` Actions secret.

## How to run it

- Push to `main` or open a pull request against `main`: the pipeline runs
  automatically.
- Manual run: the Actions tab, the `ci-quality-gates` workflow, Run workflow.
- Locally, the same test and coverage command CI runs:

  ```
  pip install -r requirements.txt -r requirements-dev.txt
  pytest --cov=heimdall --cov-report=xml --cov-report=term-missing --cov-branch
  ```

  `coverage.xml` is what the SonarCloud gate consumes.
