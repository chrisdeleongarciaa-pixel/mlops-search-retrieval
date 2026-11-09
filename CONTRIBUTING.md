# Contributing

- Install the package in a virtual environment and run the unit and contract suites from the README.
- Place an interface beside the use case that consumes it. Put identities and protocols shared across components in `contracts`.
- Inject adapters at process startup. Keep service clients and credentials at their respective boundaries.
- When a guarantee changes, update the decision, describe the adverse scenario and link observable evidence.
- Use synthetic fixtures for contract tests. Publish selected reproducible evidence with hashes. Keep credentials, weights, full corpora and local run directories outside Git.
- Run the relevant real-dependency tests for integration changes and record effective versions.
