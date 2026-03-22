# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the App

Open `tictactoe.html` directly in any browser — no build step, server, or dependencies required.

## Architecture

The entire app lives in a single file (`tictactoe.html`) with three co-located sections:

- **CSS** (`<style>`): Dark-themed UI using a fixed 3×3 CSS Grid for the board. Player X is styled in red (`#e94560`), player O in blue-grey (`#a8dadc`). The `.win` class highlights winning cells, `.taken` prevents re-clicks.
- **HTML**: Static 9-cell board with `data-i` attributes (0–8) as cell indices. Score display is separate from the board.
- **JS** (`<script>`): Purely imperative, no framework. Key pieces:
  - `WINS`: hardcoded array of 8 winning index triplets used by `checkWin()`.
  - `board`: flat 9-element array mirroring the DOM cells; `''` means empty.
  - `current`: tracks whose turn it is (`'X'` or `'O'`).
  - `score`: object `{ X, O, D }` — persists across games within the session (not stored anywhere).
  - `init()`: resets `board`, `current`, `over`, and the DOM without reloading the page.
  - Click handlers are attached once on page load to all `.cell` elements.

## Git & GitHub

- Remote: `https://github.com/farahtohme93/tictactoe`
- Always commit with descriptive messages and push after each meaningful change so the project stays backed up on GitHub.
