# Security policy

## Reporting a vulnerability

Please don't report security problems in public issues. Report them privately through GitHub: on the [Security tab](https://github.com/ferris-ai/ferris-cli/security), choose **Report a vulnerability**.

Include what you found, how to reproduce it, and what an attacker could do with it. Don't include real client documents or project state.

We'll acknowledge your report, keep you updated while we work on a fix, and credit you in the release notes unless you'd rather not be named.

## Supported versions

Fixes go into the latest release. Update with `npm install -g @ferris-ai/cli@latest`.

## Scope

ferris runs on your machine and makes no network calls. Things we especially want to hear about:

- a crafted document (email, chat export, transcript, file) that makes the scripts write outside the project's workspace, run code, or crash in a way that loses state;
- reminder hooks or install steps that change files other than the ones the README lists, or overwrite settings that aren't ferris's own;
- project data ending up anywhere other than the workspace, such as a temp folder, a log or a shared location.

The skill's instructions to the agent are part of the project too: if a document can make the agent break the skill's rules (for example, write into the state without evidence), that's in scope.
