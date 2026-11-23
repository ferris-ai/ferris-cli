"""ferris_home.py - where the project state lives (stdlib only, Python 3.9+).

The state home is a `.ferris` folder, found the way git finds `.git`: the nearest `<dir>/.ferris` walking up from the
starting folder (the current directory, or a workspace path). Inside it:

  .ferris/.gitignore          "*": git ignores the whole folder (client data is never committed)
  .ferris/projects/<slug>/    one workspace per project
  .ferris/profiles/           custom profiles
  .ferris/registry.json       projects the session-start reminder checks

PROJECT_STATE_HOME, when set, names the home directly (the ferris CLI sets it for the scripts it runs).
"""
import os

DIRNAME = ".ferris"


def find(start=None):
    """The state home for `start` (default: the current directory), or None when there is none."""
    env = os.environ.get("PROJECT_STATE_HOME")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    d = os.path.abspath(os.path.expanduser(start or os.getcwd()))
    while True:
        if os.path.basename(d) == DIRNAME:
            return d
        c = os.path.join(d, DIRNAME)
        if os.path.isdir(c):
            return c
        up = os.path.dirname(d)
        if up == d:
            return None
        d = up


def workspace_root(start=None):
    """Where a new .ferris goes: the git repository's root when inside one, else the starting folder."""
    d = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        up = os.path.dirname(d)
        if up == d:
            return os.path.abspath(start or os.getcwd())
        d = up


def home(start=None):
    """Like find(), but falls back to <workspace root>/.ferris (which may not exist yet)."""
    return find(start) or os.path.join(workspace_root(start), DIRNAME)
