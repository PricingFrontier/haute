# Installing Haute

Make sure you've [set up your environment](environment.md) first (VS Code, Python, uv).

---

## Create a project and install Haute

Open the VS Code terminal and run these commands one at a time:

```powershell
uv init my-pricing-project
cd my-pricing-project
uv add haute
haute init --target databricks
```

This creates a new project folder with everything Haute needs: a `haute.toml` configuration file, test quote templates, CI/CD workflow files, and a `.env.example` credential template.

---

## Set up a virtual environment and run

```powershell
uv venv
.venv\Scripts\activate
uv sync
haute serve
```

You'll see `(.venv)` appear in your terminal prompt after the activate step - that means the virtual environment is active. `haute serve` opens the Haute visual editor in your browser. If you see it, you're all set.

!!! info "What is a virtual environment?"
    A virtual environment is a private space for your project's packages. Without one, installing packages would change your whole computer's Python setup. With a virtual environment, each project gets its own isolated set of packages. You activate it each time you open a new terminal.

---

## Installing extras

Depending on your deploy target, you may need additional packages:

```powershell
uv add "haute[databricks]"         # Adds SQL support and pins Databricks clients
```

---

## Troubleshooting

**`haute serve` doesn't open anything in my browser**

Look at the terminal output for a line like `Running on http://localhost:8000`. Copy that address and paste it into your browser. If you see an error, make sure your virtual environment is active (`(.venv)` in your prompt).

**`(.venv)` isn't showing in my terminal prompt**

Run `.venv\Scripts\activate`. You need to do this every time you open a new terminal window.

---

## What's next?

You've got Haute running locally. From here:

- **Build a pipeline** - see the **Building Pipelines** guide to create your pricing pipeline
- **Deploy it** - when you're ready to go live, head to the [Deployment](../deployment/index.md) docs. If you're new to Git, CI/CD, and other deployment concepts, read [Before You Start](../deployment/before-you-start.md) first - it explains everything in plain English.
