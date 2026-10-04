Vendored from https://github.com/lichess-org/chessground at tag v10.4.1,
built locally from official source (not via npm - the npm registry listing
was showing a trust&safety "no longer supported" flag at vendor time; the
GitHub source itself is actively maintained, so building from there avoided
trusting a possibly-stale/flagged registry artifact).

Build commands used:
  npm install --ignore-scripts
  npx tsc --sourceMap --declaration
  npx esbuild src/chessground.ts --bundle --format=esm --outfile=dist/chessground.min.js --minify

License: GPL-3.0-or-later (see LICENSE in this directory). Fine for this
project's personal, non-distributed use per chess-trainer-mvp-plan.md.
