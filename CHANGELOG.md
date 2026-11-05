# Changelog

## 2.0.0

Created by Emmet Adriaans for Vistek Ltd.

### New
- **Customer report card.** A printable/PDF page and a Word document showing:
  - shutter life remaining, as a percentage and as estimated actuations left
  - a graded health rating with a plain-English explanation
  - how many years the shutter could last at four shooting profiles (editable)
  - a verification checklist and the inspection results
  - runtime and sensor hours for cinema cameras

  Internal data (offer, seller, PO, staff notes, fraud flags) is never shown on it.
- **Tamper-evident certificates.** Every saved evaluation gets a certificate ID and a signed verification code. The *Verify report* tab confirms that a customer's card is genuine and unaltered. Several PCs can share the signing key.
- **Exports.**
  - Excel (.xlsx): filters, frozen header, and a "By brand" summary sheet
  - Word (.docx): internal evaluation report and customer report card
  - CSV, with Excel-safe UTF-8
  - full backup (.zip)
- **Copy and paste.** Every value has a copy button. *Copy summary* gives a plain-text block; *Copy as table* pastes as a real table into Word or Excel.
- **Records.** KPI tiles (evaluations, last 30 days, purchased, average life used, serial mismatches caught), status tracking (Evaluated / Purchased / Declined / Customer report / Consignment / Trade-in), and a detail panel for viewing, editing, printing, exporting or deleting a record.
- **Settings.** Store name, address, phone, website, logo (PNG), report wording, usage profiles, signing key, data folder, backup and update check.
- **About page** with publisher credit. The Windows exe shows *Emmet Adriaans / Created for Vistek Ltd.* under Properties → Details, and has its own icon.
- **Friendly camera names** such as "Nikon Z 6II" or "Sony Alpha 7 III", with the model code kept alongside.
- **Low-confidence counters** (e.g. Fujifilm file counts) are marked as estimates on customer cards.

### Security
- Per-launch session secret in an HttpOnly, SameSite=Strict cookie. A token header is also required for every change (CSRF protection).
- Requests carrying any other Host or Origin are refused, which blocks DNS rebinding and cross-site requests. Strict Content-Security-Policy with no inline script, plus nosniff and no-framing headers.
- Uploaded files are never served as web pages; only images display inline.
- Validated record and evidence IDs. Sanitised file names. 2 GB upload cap.
- Hardened ExifTool argument file: line breaks in file names are refused, and only absolute paths are passed after `--`.
- Spreadsheet formula injection is neutralised in CSV; Excel cells are always written as text.

### Fixes and polish
- Redesigned interface: sidebar navigation, shutter-life gauge, grade chips, tabbed reading sources, Pass/Fail/N/A checks with "All pass", and a sticky save bar with a save dialog. Light and dark themes.
- Database schema versioning with automatic migration from 1.x.
- The Windows exe runs without a console window (output goes to `camcheck.log` in the data folder) and has a **Quit** button.
- Single instance: launching again just reopens the window. The app quits by itself when its window is closed, with a grace period after sleep.
- The program now uses all-new code for its Word and Excel output, with no third-party libraries.

## 1.1.0
- Native Windows USB readout (WPD/PTP); card-reader detection for SD, microSD, CFexpress A/B, CFast and XQD; per-body grouping; Sony cinema XML sidecars.

## 1.0.0
- First release: file-based shutter counts via ExifTool, serial checks, rated-life grading, intake records and printable reports.
