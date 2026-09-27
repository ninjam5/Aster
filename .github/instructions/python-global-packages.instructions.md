---
description: "Use when installing Python dependencies, choosing a Python interpreter, configuring environments, or running Python commands in this workspace. Enforce global/system package usage and avoid virtual environments."
name: "Python Global Packages Only"
applyTo: "**/*.py"
---
# Python Environment Rule

- Never create or use virtual environments such as venv, virtualenv, pipenv, poetry environments, or conda environments unless the user explicitly asks.
- Use the system/global Python interpreter configured for the workspace.
- Install dependencies into the global interpreter, not into an isolated environment.
- Prefer interpreter-bound installs for clarity, such as python -m pip install <package>.
- If setup docs suggest creating a virtual environment, adapt the steps to a global-package workflow for this project.
