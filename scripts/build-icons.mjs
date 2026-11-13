// Builds labbook/static/icons.svg (an SVG sprite) from the pinned lucide-static package.
// Add a name here, run `npm run icons`, then use {{ icon("name") }} in templates.
import { readFileSync, writeFileSync } from "node:fs";

const ICONS = [
  "archive", "archive-restore", "arrow-left-right", "ban", "bell", "bell-ring", "building-2", "calendar", "calendar-check",
  "calendar-days", "check", "check-check", "clipboard-check", "download", "history", "list", "package-x", "chevron-left", "chevron-right", "clock", "hourglass", "list-ordered",
  "package-open", "undo-2", "user-x",
  "circle", "circle-check", "circle-x", "flask-conical", "house", "image", "info", "list-checks", "lock",
  "log-out", "mail", "map-pin", "menu", "monitor", "moon", "package", "panel-left-close", "panel-left-open",
  "pencil", "plus", "search", "settings", "shield", "sun", "trash-2", "triangle-alert", "user", "users",
  "wrench", "x",
];

const symbols = ICONS.map((name) => {
  const svg = readFileSync(`node_modules/lucide-static/icons/${name}.svg`, "utf8");
  const inner = svg.replace(/^[\s\S]*?<svg[^>]*>/, "").replace(/<\/svg>\s*$/, "").replace(/\s*\n\s*/g, "");
  return `<symbol id="i-${name}" viewBox="0 0 24 24">${inner}</symbol>`;
});

writeFileSync(
  "labbook/static/icons.svg",
  `<svg xmlns="http://www.w3.org/2000/svg"><!-- Lucide icons, ISC License, https://lucide.dev -->\n${symbols.join("\n")}\n</svg>\n`,
);
console.log(`Wrote ${ICONS.length} icons to labbook/static/icons.svg`);
