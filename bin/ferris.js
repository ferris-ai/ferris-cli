#!/usr/bin/env node
import { main } from "../src/cli.js";

main(process.argv.slice(2)).then(
  (code) => {
    process.exitCode = code ?? 0;
  },
  (err) => {
    if (err && err.name === "CliError") {
      // on a terminal the spinner already showed it as a ✗ line; scripts and agents still get it on stderr
      if (!(err.shown && process.stdout.isTTY)) process.stderr.write(`ferris: ${err.message}\n`);
      process.exitCode = err.code ?? 1;
    } else {
      process.stderr.write(`ferris: ${err && err.stack ? err.stack : err}\n`);
      process.exitCode = 1;
    }
  },
);
