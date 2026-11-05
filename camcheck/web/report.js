"use strict";
// Print button for report pages (kept out of inline handlers so the CSP can forbid inline script).
const b = document.getElementById("printBtn");
if (b) b.addEventListener("click", () => window.print());
