// Terminal presentation for the human-facing commands (setup, install, new, doctor, profiles, help).
// Agent-facing commands (ferris state, ferris watch) never use this: their output stays plain JSON / script text.
// No dependencies. Color only on a TTY; NO_COLOR turns it off, FORCE_COLOR turns it on.
import { emitKeypressEvents } from "node:readline";
import readline from "node:readline/promises";

const out = process.stdout;
const env = process.env;

function colorLevel() {
  if ("NO_COLOR" in env) return 0;
  if (env.FORCE_COLOR === "0") return 0;
  if (!env.FORCE_COLOR && (!out.isTTY || env.TERM === "dumb")) return 0;
  if (/truecolor|24bit/i.test(env.COLORTERM ?? "") || ["iTerm.app", "WezTerm", "vscode", "ghostty"].includes(env.TERM_PROGRAM)) return 3;
  return 2;
}

const LEVEL = colorLevel();
export const interactive = Boolean(out.isTTY && process.stdin.isTTY && env.TERM !== "dumb" && !env.CI);
const animate = Boolean(out.isTTY && env.TERM !== "dumb" && !env.CI);

// Palette from the Ferris first-run designs: warm orange accent, soft green for done, warm greys.
const PALETTE = {
  accent: [240, 138, 69],
  deep: [198, 90, 22],
  light: [247, 178, 120],
  ok: [144, 203, 173],
  warn: [224, 176, 90],
  err: [224, 122, 106],
  dim: [138, 129, 117],
  fg: [236, 230, 220],
};

function to256([r, g, b]) {
  const c = (v) => Math.round((v / 255) * 5);
  return 16 + 36 * c(r) + 6 * c(g) + c(b);
}

function rgb(color) {
  return (s) => {
    if (!LEVEL) return String(s);
    const code = LEVEL === 3 ? `38;2;${color.join(";")}` : `38;5;${to256(color)}`;
    return `\x1b[${code}m${s}\x1b[39m`;
  };
}

const sgr = (on, off) => (s) => (LEVEL ? `\x1b[${on}m${s}\x1b[${off}m` : String(s));

export const ui = {
  color: LEVEL > 0,
  interactive,
  accent: rgb(PALETTE.accent),
  ok: rgb(PALETTE.ok),
  warn: rgb(PALETTE.warn),
  err: rgb(PALETTE.err),
  dim: rgb(PALETTE.dim),
  fg: rgb(PALETTE.fg),
  bold: sgr(1, 22),
  italic: sgr(3, 23),
};

const stripAnsi = (s) => String(s).replace(/\x1b\[[0-9;]*m/g, "");
export const width = (s) => [...stripAnsi(s)].length;

// ---------------------------------------------------------------- brand

const LOGO = [
  "███████╗███████╗██████╗ ██████╗ ██╗███████╗",
  "██╔════╝██╔════╝██╔══██╗██╔══██╗██║██╔════╝",
  "█████╗  █████╗  ██████╔╝██████╔╝██║███████╗",
  "██╔══╝  ██╔══╝  ██╔══██╗██╔══██╗██║╚════██║",
  "██║     ███████╗██║  ██║██║  ██║██║███████║",
  "╚═╝     ╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝╚══════╝",
];

function mix(a, b, t) {
  return a.map((v, i) => Math.round(v + (b[i] - v) * t));
}

/** The wordmark with a light-to-deep orange sweep; the shadow strokes are drawn dimmer. */
function logoLines() {
  const w = LOGO[0].length;
  return LOGO.map((row, y) =>
    [...row]
      .map((ch, x) => {
        if (ch === " " || !LEVEL) return ch;
        const t = Math.min(1, (x / w) * 0.85 + (y / LOGO.length) * 0.15);
        const c = mix(PALETTE.light, PALETTE.deep, t);
        return rgb(/[╗╔╝╚═║]/.test(ch) ? mix(c, [40, 34, 28], 0.45) : c)(ch);
      })
      .join(""),
  );
}

export function banner({ version, tagline = "project state for Claude Code, Codex and Cursor", compact = false } = {}) {
  const cols = out.columns || 80;
  const lines = [""];
  if (compact || cols < 50 || !LEVEL) {
    lines.push(`  ${ui.accent("●")} ${ui.bold("ferris")}  ${ui.dim(tagline)}${version ? ui.dim(` · v${version}`) : ""}`);
  } else {
    for (const l of logoLines()) lines.push(`  ${l}`);
    lines.push("");
    lines.push(`  ${ui.accent("●")} ${ui.fg(tagline)}${version ? ui.dim(`   v${version}`) : ""}`);
  }
  lines.push("");
  return lines.join("\n");
}

// ---------------------------------------------------------------- layout helpers

/** ● Check   ● Skill   ○ Project   ○ Reminders  (done green, current orange, todo dim). */
export function stepBar(steps, current) {
  return (
    "  " +
    steps
      .map((s, i) => (i < current ? `${ui.ok("●")} ${ui.dim(s)}` : i === current ? `${ui.accent("●")} ${ui.bold(s)}` : ui.dim(`○ ${s}`)))
      .join("   ")
  );
}

export function bar(fraction, size = 18) {
  const n = Math.max(0, Math.min(size, Math.round(fraction * size)));
  return ui.accent("━".repeat(n)) + ui.dim("─".repeat(size - n));
}

/** A rounded box around lines, for the closing "what next" card. */
export function box(lines, { title } = {}) {
  const inner = Math.max(...lines.map(width), title ? width(title) + 3 : 0) + 2;
  const top = title
    ? ui.dim("╭─ ") + ui.accent(title) + ui.dim(` ${"─".repeat(inner - width(title) - 3)}╮`)
    : ui.dim(`╭${"─".repeat(inner)}╮`);
  const body = lines.map((l) => `${ui.dim("│")} ${l}${" ".repeat(inner - width(l) - 1)}${ui.dim("│")}`);
  return [top, ...body, ui.dim(`╰${"─".repeat(inner)}╯`)].map((l) => `  ${l}`).join("\n");
}

export const line = (s = "") => out.write(`${s}\n`);
export const ok = (s) => line(`  ${ui.ok("✓")} ${s}`);
export const warn = (s) => line(`  ${ui.warn("!")} ${s}`);
export const fail = (s) => line(`  ${ui.err("✗")} ${s}`);
export const info = (s) => line(`  ${ui.dim("·")} ${s}`);

// ---------------------------------------------------------------- spinner

const FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];
let cursorHidden = false;
function hideCursor() {
  if (!animate || cursorHidden) return;
  cursorHidden = true;
  out.write("\x1b[?25l");
  process.once("exit", () => out.write("\x1b[?25h"));
}
function showCursor() {
  if (cursorHidden) out.write("\x1b[?25h");
  cursorHidden = false;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * Run fn() behind a spinner. Shows "✓ done text" (or "✗") when it settles. A short floor (minMs) keeps very fast
 * steps readable on a TTY; off a TTY nothing animates and only the final line is printed.
 */
export async function spin(text, fn, { done, minMs = 350 } = {}) {
  const start = Date.now();
  let i = 0;
  let label = text;
  const draw = () => {
    const secs = (Date.now() - start) / 1000;
    const t = secs >= 2 ? ui.dim(` ${secs.toFixed(0)}s`) : "";
    out.write(`\r\x1b[2K  ${ui.accent(FRAMES[i++ % FRAMES.length])} ${label}${t}`);
  };
  let timer;
  if (animate) {
    hideCursor();
    draw();
    timer = setInterval(draw, 80);
  }
  const clear = () => {
    if (timer) clearInterval(timer);
    if (animate) out.write("\r\x1b[2K");
    showCursor();
  };
  try {
    const result = await fn({ update: (s) => (label = s) });
    if (animate) await sleep(Math.max(0, minMs - (Date.now() - start)));
    clear();
    const msg = typeof done === "function" ? done(result) : done === undefined ? text : done;
    if (msg) ok(msg);
    return result;
  } catch (e) {
    clear();
    fail(`${text}: ${ui.err(e.message ?? e)}`);
    if (e && typeof e === "object") e.shown = true; // the CLI's error handler does not print it a second time
    throw e;
  }
}

// ---------------------------------------------------------------- prompts

let rl;
let closing = false;

function cancelled() {
  showCursor();
  out.write(`\n  ${ui.dim("Cancelled. Nothing else was changed.")}\n`);
  process.exit(130);
}

function reader() {
  if (!rl) {
    rl = readline.createInterface({ input: process.stdin, output: out });
    // Ctrl-C: readline would otherwise only pause. Ctrl-D (end of input) mid-question: stop instead of exiting 0 silently.
    rl.on("SIGINT", cancelled);
    rl.on("close", () => {
      if (!closing) cancelled();
    });
  }
  return rl;
}

export function closePrompts() {
  closing = true;
  rl?.close();
  rl = undefined;
  closing = false;
}

const prompt = async (q) => (await reader().question(q)).trim();

export async function ask(question, { fallback = "", hint } = {}) {
  const h = hint ?? (fallback ? fallback : "");
  const a = await prompt(`  ${ui.accent("?")} ${ui.bold(question)}${h ? ui.dim(` (${h})`) : ""} `);
  return a || fallback;
}

export async function confirm(question, { yes = true } = {}) {
  const a = (await prompt(`  ${ui.accent("?")} ${ui.bold(question)} ${ui.dim(yes ? "(Y/n)" : "(y/N)")} `)).toLowerCase();
  if (!a) return yes;
  return a === "y" || a === "yes";
}

// Arrow-key pickers: ↑/↓ (or j/k) move, space ticks, Enter confirms; number keys jump. Off a raw-capable terminal they
// fall back to typed numbers, so piped input and tests keep working.
const rawOk = () => interactive && typeof process.stdin.setRawMode === "function";

function pick(question, options, { multi, cursor = 0 }) {
  closePrompts(); // readline would read the same keys
  const ticked = options.map((o) => Boolean(o.checked));
  const labelW = Math.max(...options.map((o) => width(o.label))) + 2;
  const help = multi ? "↑↓ move · space select · a all · enter confirm" : "↑↓ move · enter select";
  let drawn = 0;
  const render = () => {
    const rows = [`  ${ui.accent("?")} ${ui.bold(question)}`];
    options.forEach((o, i) => {
      const here = i === cursor;
      const ptr = here ? ui.accent("❯") : " ";
      const box = multi ? (ticked[i] ? ui.ok("◉") : ui.dim("◯")) + " " : "";
      const label = o.label + " ".repeat(Math.max(0, labelW - width(o.label)));
      rows.push(`  ${ptr} ${box}${here ? ui.accent(label) : label}${ui.dim(o.hint ?? "")}`);
    });
    rows.push(`    ${ui.dim(help)}`);
    if (drawn) out.write(`\x1b[${drawn}A`);
    out.write(rows.map((r) => `\r\x1b[2K${r}`).join("\n") + "\n");
    drawn = rows.length;
  };
  return new Promise((resolve) => {
    const stdin = process.stdin;
    emitKeypressEvents(stdin);
    stdin.setRawMode(true);
    stdin.resume();
    hideCursor();
    const finish = (value, shown) => {
      stdin.off("keypress", onKey);
      stdin.setRawMode(false);
      stdin.pause();
      out.write(`\x1b[${drawn}A\x1b[0J`);
      line(`  ${ui.ok("✓")} ${ui.bold(question)} ${ui.accent(shown || ui.dim("none"))}`);
      showCursor();
      resolve(value);
    };
    const onKey = (str, key = {}) => {
      if (key.ctrl && (key.name === "c" || key.name === "d")) {
        stdin.setRawMode(false);
        out.write(`\x1b[${drawn}A\x1b[0J`);
        cancelled();
      }
      const n = options.length;
      if (key.name === "up" || key.name === "k") cursor = (cursor - 1 + n) % n;
      else if (key.name === "down" || key.name === "j" || key.name === "tab") cursor = (cursor + 1) % n;
      else if (/^[1-9]$/.test(str ?? "") && Number(str) <= n) {
        cursor = Number(str) - 1;
        if (multi) ticked[cursor] = !ticked[cursor];
      } else if (multi && key.name === "space") ticked[cursor] = !ticked[cursor];
      else if (multi && key.name === "a") ticked.fill(!ticked.every(Boolean));
      else if (key.name === "return" || key.name === "enter") {
        if (!multi) return finish(options[cursor].value, options[cursor].label);
        const chosen = options.filter((_, i) => ticked[i]);
        return finish(chosen.map((o) => o.value), chosen.map((o) => o.label).join(", "));
      } else return;
      render();
    };
    stdin.on("keypress", onKey);
    render();
  });
}

/** Tick several; the ones with checked: true start ticked. Returns the chosen values. */
export async function chooseMany(question, options) {
  if (rawOk()) return pick(question, options, { multi: true });
  line(`  ${ui.accent("?")} ${ui.bold(question)}`);
  options.forEach((o, i) =>
    line(`    ${ui.accent(String(i + 1))}  ${o.checked ? ui.ok("[x]") : ui.dim("[ ]")} ${ui.bold(o.label.padEnd(12))} ${ui.dim(o.hint ?? "")}`),
  );
  for (;;) {
    const a = await prompt(`    ${ui.dim("Enter for the ticked ones, or numbers like 1,3")} `);
    if (!a) return options.filter((o) => o.checked).map((o) => o.value);
    const picks = a.split(/[\s,]+/).filter(Boolean).map(Number);
    if (picks.length && picks.every((n) => Number.isInteger(n) && n >= 1 && n <= options.length)) {
      return [...new Set(picks)].map((n) => options[n - 1].value);
    }
  }
}

/** Pick one; returns the chosen value. */
export async function choose(question, options, { fallback = 0 } = {}) {
  if (rawOk()) return pick(question, options, { multi: false, cursor: fallback });
  line(`  ${ui.accent("?")} ${ui.bold(question)}`);
  options.forEach((o, i) => line(`    ${ui.accent(String(i + 1))}  ${ui.bold(o.label.padEnd(12))} ${ui.dim(o.hint ?? "")}`));
  for (;;) {
    const a = await prompt(`    ${ui.dim(`choose 1-${options.length} (${fallback + 1})`)} `);
    if (!a) return options[fallback].value;
    const n = Number(a);
    if (Number.isInteger(n) && n >= 1 && n <= options.length) return options[n - 1].value;
    const byName = options.find((o) => o.label === a || o.value === a);
    if (byName) return byName.value;
  }
}
