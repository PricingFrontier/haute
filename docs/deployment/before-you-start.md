# Before You Start

If you're a pricing analyst, some of the terminology in these docs will be unfamiliar. This page explains the key concepts you'll encounter - **no prior technical knowledge assumed**.

You can skip this page if you're already comfortable with the command line and Git. Otherwise, read it once before diving into the target-specific guides.

---

## Prerequisites

Before you can deploy, you need:

- **VS Code**, **Python 3.11+**, and **uv** installed on your machine - see **[Getting Started](../getting-started/index.md)** if you haven't done this yet
- **Git** installed - download from [git-scm.com](https://git-scm.com/download/win) and run the installer (accept the defaults). To check it's installed, type `git --version` in your terminal.

---

## The terminal

The **terminal** (also called the **command line** or **command prompt**) is a text-based way to talk to your computer. Instead of clicking buttons in a graphical interface, you type commands and press Enter.

### How to open it

The easiest way is to use the **built-in terminal in VS Code**:

1. Open VS Code
2. Go to **Terminal** → **New Terminal** in the menu bar
3. A terminal panel opens at the bottom of the VS Code window

Alternatively, you can open a standalone terminal by pressing ++win+r++, typing `cmd`, and pressing Enter.

You'll see a blinking cursor waiting for you to type something. This is where you'll run Haute commands.

!!! note "PowerShell, Command Prompt, or bash?"
    The VS Code terminal on Windows defaults to **PowerShell**. All the commands in these docs work in PowerShell. You don't need to switch to anything else. If you see a command in these docs inside a code block, just type it into your VS Code terminal and press Enter.

### Running a command

When the docs show something like this:

```powershell
haute serve
```

It means: type `haute serve` into your terminal and press **Enter**. The output will appear below your command.

### Common commands you'll see in these docs

| Command | What it does | Plain English |
|---|---|---|
| `git clone <url>` | Downloads a project | "Get a copy of this project onto my computer" |
| `cd my-project` | Changes directory | "Go into the `my-project` folder" |
| `dir` | Lists files | "Show me what's in this folder" |
| `copy .env.example .env` | Copies a file | "Make a copy of `.env.example` and name it `.env`" |
| `type .env` | Prints a file's contents | "Show me what's inside `.env`" |
| `uv add haute` | Installs a Python package | "Download and install Haute into this project" |
| `uv sync` | Installs all project dependencies | "Install everything this project needs" |
| `curl http://...` | Makes a web request | "Send a request to this web address and show me the response" |

!!! tip "You don't need to memorise these"
    The Haute docs will always show you the exact command to type. You just need to know where to type it (the terminal) and how to run it (type it and press Enter).

---

## Getting your project ready for deployment

If you've been playing around with Haute locally (following [Getting Started](../getting-started/index.md)), your project lives in a folder on your machine. To deploy it, you need to put it in a **repository**.

A **repository** (or **repo**) is a project folder with a superpower: it tracks the full history of every change anyone makes. Think of it like a shared drive folder, but one that remembers every version of every file, who changed what, and when - so you can always see exactly what changed, undo mistakes, and work alongside colleagues without overwriting each other's work.

The tool that manages this is called **Git**. Your repository lives in a folder on your computer, and a copy also lives on a hosting service like **GitHub**, **GitLab**, or **Azure DevOps** - which is where your team shares it. When you make changes, you **push** them to the shared copy. When someone else makes changes, you **pull** them down. This is how the whole team stays in sync.

Haute needs a repository because the entire deployment process is built around it. When you push a change and merge it into the main version, that's what triggers the automated testing and deployment pipeline. No repository means no CI/CD, no safety checks, and no deployments.

### Getting access to the repository

Someone on your team (a tech lead, data engineer, or IT) creates the repository and gives you access. If you don't have one yet, ask: *"I need a Git repository for my pricing project - can you create one and give me access?"*

They'll send you a link that looks something like:

- **GitHub:** `https://github.com/yourcompany/motor-pricing.git`
- **GitLab:** `https://gitlab.com/yourcompany/motor-pricing.git`
- **Azure DevOps:** `https://dev.azure.com/yourcompany/motor-pricing/_git/motor-pricing`

### Cloning an existing project

If your team already has a repository, **clone** it - this downloads a copy to your computer. Open the VS Code terminal and run:

```powershell
git clone https://github.com/yourcompany/motor-pricing.git
```

(Replace the URL with the one your team gave you.)

This creates a folder with all the project files. Now go into it, create a virtual environment, and install the dependencies:

```powershell
cd motor-pricing                   # Go into the project folder
uv venv                            # Create a virtual environment
.venv\Scripts\activate             # Activate the virtual environment
uv sync                            # Install all the project's dependencies
```

You'll see `(.venv)` appear at the start of your terminal prompt after the activate step - that means it's active. Run `haute serve` to check everything works.

### Common setup problems

**`uv sync` prints a wall of red text**

This usually means a Python version mismatch. Check which Python version the project requires (look for `requires-python` in `pyproject.toml`) and make sure your installed version matches. Run `python --version` to check. If it says 3.10 but the project needs 3.11+, download a newer Python from [python.org](https://www.python.org/downloads/).

**`git clone` asks for a username and password**

This means Git can't authenticate you automatically. Enter your GitHub/GitLab/Azure DevOps username and a **personal access token** (not your regular password). If you don't have a token, ask your IT team - they'll either create one for you or help you set up credential caching.

---

## Your project files

When you open your project folder, you'll see files like these:

| File | What it is |
|---|---|
| `haute.toml` | Your deployment configuration - what gets deployed and where |
| `main.py` | Your pricing pipeline |
| `.env` | Your credentials (passwords/tokens) for calling the live API locally - **never shared or committed** |
| `.env.example` | A template showing which credentials are needed - safe to share. Give this to whoever sets up CI secrets. |
| `.gitignore` | A list of files that Git should **not** track (like `.env`) |
| `tests/quotes/` | Test data used to validate your pipeline before deploying |

!!! info "When do I need a `.env` file?"
    You need a `.env` file on your laptop if you want to **call the live endpoint locally** - for example, to run your own impact comparisons against the current production model before pushing your changes. To create one, copy the template and fill in the values:

    ```powershell
    copy .env.example .env
    ```

    Then open `.env` in VS Code and fill in the values (e.g. your Databricks workspace URL and token). The `.gitignore` file ensures `.env` is **never committed or shared** - your passwords stay on your machine only.

    The same credentials also need to be set up as **encrypted secrets in your CI provider** (GitHub Secrets, GitLab CI/CD Variables, or Azure DevOps Variable Groups) so that the automated deploy pipeline can use them. Your IT team or tech lead usually handles that part - give them the `.env.example` file.

### Gitignore: keeping secrets safe

The `.gitignore` file tells Git to ignore certain files - they won't be tracked, shared, or uploaded. Haute automatically adds `.env` to `.gitignore` so your passwords and tokens stay on your machine only.

You don't need to edit `.gitignore` - just know that it's there to protect you.

---

## Git workflow: branches, pull requests, and merging

When you work with a team, changes go through a review process before they go live. Here's how it works in plain English:

### 1. Create a branch

A **branch** is like making a copy of the whole project to try something out. Your changes only exist on your branch - the main version is untouched.

```powershell
git checkout -b my-new-rates
```

This creates a new branch called `my-new-rates`. Don't worry about what `checkout -b` means - it's just the Git command for "create a new branch and switch to it." You just need to replace `my-new-rates` with a short name describing your change. Any changes you make now are on this branch.

### 2. Save your changes (commit)

A **commit** is saving a checkpoint of your changes with a short description. Think of it like saving a document with a note about what you changed.

```powershell
git add .
git commit -m "Updated motor frequency model"
```

`git add .` means "include all my changed files" and `git commit -m "..."` saves them with a message. You don't need to memorise the syntax - just copy the pattern above and change the message in quotes.

### 3. Upload your changes (push)

**Pushing** uploads your branch to the shared repository (on GitHub/GitLab/Azure DevOps) so others can see it. The shared repository already exists on the hosting service (see [Getting your project ready](#getting-your-project-ready-for-deployment)) - `git push` just sends your latest changes to it.

```powershell
git push
```

!!! info "First time pushing? Git will ask you to log in"
    The very first time you run `git push`, Git needs to verify who you are. A **browser window will open** asking you to sign in to your GitHub, GitLab, or Azure DevOps account. Sign in with your normal work credentials and you're done - Git remembers you after this, so you won't be asked again.

    If no browser window opens and the terminal asks for a username/password, enter your account username and a **personal access token** (not your normal password). Ask your IT team if you're unsure - they can help you through this the first time.

### 4. Ask for a review (pull request)

A **pull request** (PR) - called a **merge request** (MR) on GitLab - is asking your teammate to review your changes before they go into the main version. You create this on the website, not in the terminal.

After you push, go to your project page in your browser:

- **GitHub:** You'll see a yellow banner at the top saying *"my-new-rates had recent pushes - Compare & pull request."* Click that button. Give your PR a title (e.g. "Updated motor frequency model"), then click **Create pull request**.
- **GitLab:** You'll see a blue banner saying *"Create merge request."* Click it, give it a title, then click **Create merge request**.
- **Azure DevOps:** Go to **Repos** → **Pull requests** → **New pull request**. Select your branch, give it a title, then click **Create**.

Your reviewer can see exactly what you changed, leave comments, and approve or request changes.

### 5. Accept the changes (merge)

Once approved, you **merge** the pull request. This applies your changes to the main version of the project. On the CI/CD pages, "merge to main" means this step.

!!! info "Why bother with all this?"
    The branch → review → merge process adds a safety layer: someone else checks your work before it reaches production. For pricing, this means a second pair of eyes on rate changes before they affect real quotes.

### If you're working solo

If you're the only person on the project, you can still use branches and pull requests (reviewing your own changes), or you can push directly to main. Haute works either way.

---

## What is an API?

An **API** (Application Programming Interface) is a way for computer systems to talk to each other. A deployed Haute pipeline can be served as an API: Databricks deployment creates or updates a Model Serving endpoint, while the generic container target produces the image that your hosting platform must run.

**Example:** Your policy admin system needs a premium for a new quote. It sends the quote data to your API's web address, and the API runs your pipeline and sends back the premium. No human involved - it happens automatically in milliseconds.

In these docs, when you see **endpoint**, it means the specific web address of your API. For example:

```
https://adb-xxx.azuredatabricks.net/serving-endpoints/motor-pricing/invocations
```

That's the endpoint. Other systems send requests to that address to get premiums.

---

## What is `curl`?

`curl` is a command-line tool for sending web requests. When the docs show a `curl` command, it's just a way to test that your API is working - like visiting a website, but from the terminal.

```powershell
curl http://localhost:8080/health
```

This sends a request to your API and shows the response. If you see `{"status": "ok"}`, it's working.

**You don't have to use `curl`.** Every `curl` example in these docs is just for testing. Your real systems (policy admin, quote engines) will call the API using their own code. If you prefer, you can test with Python instead:

```python
import requests
response = requests.get("http://localhost:8080/health")
print(response.json())
```

---

## What are "staging" and "production"?

Many release processes use **two copies** of an API:

- **Staging** is a **private test copy** that only your team can see. It is an infrastructure and release-process choice: you configure a separate endpoint name (often with `--endpoint-suffix "-staging"`) and arrange for it to be deployed and protected by your target platform.
- **Production** is the **real one** that your policy admin system calls. It serves actual quotes to customers.

Haute does **not** automatically create a staging environment, wait for an asynchronously provisioned endpoint, require an approval, or promote a deployment. Its generated CI examples run staging deploy, smoke, and impact commands in sequence, but your CI-provider configuration controls approvals and production access. For a newly created Databricks endpoint, add a readiness wait before smoke testing; for container targets, arrange the hosting handoff before those commands. Set branch protection and environment rules before treating that sequence as a release gate.

When you see "deploys to staging" in these docs, it means: "runs the selected target's staging deployment step." That creates or updates a test endpoint only when the target supports it and the endpoint is ready. When you see "promotes to production," it means: "runs the production deployment step configured by your release process."

---

## What is CI/CD?

**CI/CD** stands for **Continuous Integration / Continuous Deployment**. In plain English:

- **CI** = your code is automatically tested every time you propose a change
- **CD** = your CI workflow can run the deployment step after those tests pass; whether that creates or updates a live endpoint depends on the selected target and your hosting process

The generated CI files are an example command sequence. Once you enable and adapt them, they can run a checklist such as:

1. ✓ Code style is correct
2. ✓ Tests pass
3. ✓ Pipeline parses correctly
4. ✓ Test quotes score successfully
5. → Deploy to a staging (test) endpoint if your target supports it
6. → Smoke test an already-running staging endpoint
7. → Generate an impact report from existing staging and production endpoints
8. → A human follows the approval process configured by the CI provider

CI/CD can make those checks repeatable, but only the policies and controls you configure in the CI provider make them a release gate.

The CI/CD system runs on a service like **GitHub Actions**, **GitLab CI/CD**, or **Azure DevOps Pipelines** - you don't need to install anything on your machine. `haute init` generates starter configuration files; review and tailor them before use.

---

## Quick glossary

| Term | Plain English |
|---|---|
| **Terminal / command line** | A text window where you type commands instead of clicking buttons |
| **Repository (repo)** | A project folder that tracks the history of every change |
| **Git** | The tool that manages repositories and change history |
| **Branch** | A draft copy of the project for trying out changes |
| **Commit** | Saving a checkpoint of your changes |
| **Push** | Uploading your changes to the shared copy of the project |
| **Clone** | Downloading a copy of an existing project to your computer |
| **Pull request (PR)** | Asking a teammate to review your changes before they go live |
| **Merge** | Accepting reviewed changes into the main version |
| **API / endpoint** | A web address that other systems call to get premiums |
| **REST API** | A specific type of API that uses standard web addresses and JSON data |
| **Staging** | A private test copy of your API that only your team can see - your target and release process must provide it before Haute can test it |
| **Production** | The real, live API that your policy admin system calls |
| **CI/CD** | Automated testing and deployment that runs every time you make a change |
| **`.env` file** | A file on your machine that stores passwords and tokens (never shared) |
| **`.gitignore`** | A file that tells Git which files to not track (like `.env`) |
| **`curl`** | A command-line tool for testing web requests (optional - you can use Python instead) |
| **Docker / container** | A package that bundles your app and everything it needs to run |
| **Port** | A number that identifies which service on a computer to talk to (like an extension number on a phone system) |
| **Registry** | An online storage service for Docker images (like a shared drive for containers) |
