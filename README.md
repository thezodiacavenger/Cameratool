# Camera Check: Used Camera Intake, Verification & Report Cards (v2.0)

*Created by Emmet Adriaans for Vistek Ltd.*

A desktop tool for a camera store's buying desk. It reads a camera's shutter/actuation
count, serial number and cinema runtime **over USB or from the memory card**. It then:

- confirms the camera is the body you are holding
- grades shutter wear against the rated life
- saves an evaluation record
- issues the customer a **tamper-evident report card**

Everything runs locally on the store PC, and no customer data leaves the machine.

---

## Install (Windows, no Python needed)
1. Download **CameraCheck-Windows.zip** from the latest release:
   https://github.com/thezodiacavenger/Cameratool/releases/latest
2. Unzip it anywhere and double-click **CameraCheck.exe**. Keep the folder together.
   It opens in its own window. To exit, click **Quit** at the bottom of the sidebar; it also closes by
   itself about 10 minutes after its window is closed. Launching it again while it is running just
   reopens the window.
3. On first launch, Windows SmartScreen may say *Windows protected your PC*. Click
   **More info → Run anyway**. This warning appears because the exe is not code-signed (see below).

Developers can run from source instead: `Start Camera Check (Windows).bat` (Python 3) or
`Start Camera Check (Mac).command`.

## Daily use
1. **Intake.** Type the serial from the body label first. Every reading is checked against it.
2. **Read the camera**, using one of the three tabs:
   - **Card reader:** inserted cards are detected and scanned automatically.
   - **USB cable:** reads live counters where the camera provides them, and downloads the newest photo to decode.
   - **Files:** drag and drop image or clip files.
3. **Manual readings.** Enter service-menu counts and cinema runtime/sensor hours, and attach a photo of the screen.
4. **Condition and offer.** Choose a cosmetic grade, mark the Pass/Fail/N/A checks, and add internal notes.
5. **Save.** The save dialog gives you:
   - the customer report card (print or save as PDF)
   - the report card as a Word file
   - the internal report (web page or Word)
   - a "copy summary" button

### Copying information into other documents
- Every value (serial, count, firmware, codes) has a copy button.
- **Copy summary** copies a plain-text block for emails or tickets.
- **Copy as table** copies a formatted table that pastes into Word or Excel as a real table.

### Exports
| What | Where |
|---|---|
| All records to **Excel** (.xlsx), with filters and a "By brand" summary sheet | Records → Excel (follows the current search/status filter) |
| All records to **CSV** | Records → CSV |
| One record as an **internal evaluation** (Word or PDF) | Record panel / save dialog |
| One record as a **customer report card** (Word or PDF) | Record panel / save dialog |
| **Full backup** (database, settings, logo, evidence photos) | Settings → Download full backup |

### Customer report card
The report card shows:
- **shutter life remaining**, as a percentage and as estimated actuations left
- an **A–F health grade** with a plain-English explanation
- **how many years** the shutter could last at different shooting volumes (profiles are editable in Settings)
- the verification checklist: serial matched, read directly from the camera, fresh capture
- the inspection results, plus runtime and sensor hours for cinema cameras

Counters that are only a lower bound, such as Fujifilm file counts, are labelled as estimates.

Internal information (offer, seller, PO, staff notes, fraud flags) is never shown on the card.

Each card carries a **certificate ID** and a **verification code** signed with the store's key.
In the **Verify report** tab, staff can confirm a card is genuine and unaltered. For example,
when a camera you sold comes back as a trade-in, or a buyer asks about one sold privately.
To let several PCs verify each other's cards, copy the signing key between them
(Settings → Certificate signing key).

## Reading a camera

### 1. Card reader
- Insert the card. The **Card reader** panel lists every card within about 3 seconds.
- It recognises the folder layouts for:
  - **DCIM**: stills cameras
  - **Sony M4ROOT / XDROOT**: FX6, FX9, FX3, Venice, Burano
  - **Canon CONTENTS**: Cinema EOS
  - **RED RDM/RDC**
  - **ARRI / Blackmagic**: clips in the card root
- With **auto-scan** on, a new camera card is read straight away.
- It reads the **newest** photos and clips and groups them **per camera body**, because a seller's card often holds files from several bodies.
- CFexpress readers are NVMe-over-USB, and Windows often reports them as fixed disks instead of removable ones. They are still detected, because every non-system drive is checked for camera folders.
- Cameras that offer a *Mass Storage* or *USB card reader* mode (Sony, Fujifilm, OM System, Panasonic) show up here too.

### 2. USB cable (Windows: native, no extra drivers)
Camera Check talks PTP to the camera through the standard Windows camera driver
(Windows Portable Devices). You do **not** need Zadig, libusb or the maker's software.
It does two things:

| What | How |
|---|---|
| **Live counter** from the camera | Canon EOS ShutterCounter (PTP 0xD1AC), Fujifilm TotalShotCount (0xD310), Olympus/OM ShutterActuationCount (0xD059, experimental), when the body exposes them. Serial and firmware come from every body. |
| **Newest photo pulled from the camera** | Downloaded over USB and decoded with ExifTool. This is how Nikon, Sony, Pentax and Canon mirrorless counts are read over USB, because those makers store the count in image files instead of as a USB property. |

The app cross-checks both sources. If the serial reported over USB differs from the
serial in the newest photo, the photo on the card did not come from this body, and
the app shows a hard fail.

**Before connecting:**
- Close Windows Photos, Lightroom import and EOS Utility, because only one program can talk to a camera at a time.
- Set the camera's USB mode as follows (also listed in the app under *USB mode per brand*):

| Brand | USB setting |
|---|---|
| Nikon | MTP/PTP |
| Canon | Default |
| Sony | MTP or PC Remote |
| Fujifilm | USB tether / PC auto, for the live count |
| OM System | MTP / tether |
| Panasonic | PTP / tether |

### 3. Manual readings and evidence photos
Use this for counters that live only in service menus (OM System hidden menu, Panasonic,
Leica, Hasselblad) and for **cinema runtime and sensor hours**. Enter the values and
attach a photo of the screen. The in-app **Brand guide** has the menu paths.

## Security
- The app only listens on this PC (127.0.0.1) and refuses requests that use any other host name.
- A random session secret is created each launch. It is stored in an HttpOnly, SameSite=Strict cookie, and a
  matching token header is required for every change, so other websites cannot use the app.
- A strict Content-Security-Policy blocks inline script. Uploaded files are never served as web pages.
- Record IDs, evidence IDs and file names are validated, and uploads are capped at 2 GB.
- Spreadsheet exports are protected against formula injection.

## Publisher and code signing
The exe's **Properties → Details** shows *Emmet Adriaans* and *Created for Vistek Ltd.*
Windows SmartScreen's *Publisher* line only comes from a **code-signing certificate**. To remove the
"unknown publisher" warning, buy an OV or EV code-signing certificate (or use Azure Trusted Signing)
in the name of Emmet Adriaans or Vistek Ltd. The build workflow can then sign the exe automatically.

## Automatic checks
- **Serial match:** the serial in the file or from USB must match the serial on the body label. A mismatch is a hard fail.
- **USB vs. card:** the serial over USB must match the serial in the newest photo.
- **Missing MakerNotes:** the file was exported or re-saved, so the counters were stripped. Hard fail.
- **Edited in software:** Lightroom, Photoshop, Capture One and similar.
- **Stale photo:** more than 2 days old.
- **Counter going backwards** between photos.
- **Store history:** if this serial was bought before with a **higher** count, the counter was reset or the board was swapped.
- **Rated life:** % used and an A–F grade. Bodies with no mechanical shutter (Nikon Z8/Z9) are flagged as such.

## Brand coverage: honest summary

| Brand | Card / file | USB | Notes |
|---|---|---|---|
| Nikon | ✅ ShutterCount (+ MechanicalShutterCount on newer Z bodies) | ✅ via newest photo | Most reliable |
| Sony | ✅ ShutterCount + ImageCount | ✅ via newest photo | Some early models wrap at 65,536 |
| Pentax | ✅ | ✅ via newest photo | |
| Canon | ⚠️ R5, R6, R6 II, RP family, R50 V, 1D III only | ✅ live counter on EOS bodies that expose it | Older DSLRs that expose neither need a service readout |
| Fujifilm | ⚠️ ImageCount, a lower bound only | ✅ live TotalShotCount in tether mode | Use USB for Fuji |
| OM System / Olympus | ❌ | ⚠️ experimental live counter | Hidden service menu otherwise |
| Panasonic, Leica, Hasselblad | Serial only | Serial only | Service report / menu screen |
| Cinema: ARRI, RED, Sony, Canon, Panasonic, Blackmagic | Model + serial from clips | – | Runtime/sensor hours come from the menu, entered manually with a photo |

**No single tool can read a definitive count from every brand.** Some manufacturers
only expose the true count to their service centres. For those brands, the app makes
sure the evidence is captured and attached to the record.

**Testing status.** The protocol code is tested against a simulated camera in the
included test suite. It has not yet been tested on physical cameras. Before relying
on it, check one body of each brand you buy most against a known count. If a USB read
fails, open **USB diagnostic log** on the result and keep it; it shows exactly which
step the camera refused.

## Store procedure (SOP)
1. Record the serial from the body label first.
2. Use the **store's own card**. Format it in the camera and shoot 2 frames with the mechanical shutter.
3. Read the camera over **USB**, or move the card to the **card reader**. Never accept files the seller supplies, because counters inside files can be rewritten.
4. The serial match must show green.
5. For brands with no counter in their files, follow the Brand guide, photograph the counter screen and attach it.
6. Complete the checklist and save. Print or save the report as a PDF to the purchase file.

## Data
- Records, evidence files and store rating overrides are kept in:
  - Windows: `%APPDATA%\CameraCheck`
  - macOS: `~/Library/Application Support/CameraCheck`
- **Records → Export CSV** exports every record for Excel or your inventory system.
- Rated shutter life: `camcheck/data/ratings.json`. Figures marked *reported* are not manufacturer figures; add store overrides in the app.
- Brand procedures: `camcheck/data/guides.json`. These are editable.
- Optional: `CAMCHECK_CARD_ROOTS` lists extra folders to watch as cards, for example network card readers.

## Credits
ExifTool by Phil Harvey (https://exiftool.org) is used as an external program.
The PTP vendor property codes come from the libgphoto2 project (LGPL). On Windows,
comtypes provides the COM bridge to Windows Portable Devices.
