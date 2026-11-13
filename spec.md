# LabBook — Product & Technical Specification

**Version:** 1.0 (draft)
**Date:** 2026-11-13
**Status:** Ready for implementation planning

---

## 1. Overview

LabBook is a responsive web app where students, teachers and lab staff in a single department book shared lab equipment. It replaces paper sign-up sheets and spreadsheets with one place to see what's available, book it, approve it where needed, record pickup and return, and report on usage.

### 1.1 Goals

- Prevent double-booking of shared equipment.
- Let students and teachers book equipment from a phone or desktop in under a minute.
- Give lab technicians a simple daily view of check-outs and returns.
- Enforce fair-use rules automatically: quotas, booking windows, duration caps and no-show penalties.
- Give admins data on equipment use.

### 1.2 Non-goals (v1)

- More than one department or institution (multi-tenancy).
- SSO (Google or Microsoft). v1 uses email and password only.
- Bookings for a whole class or group.
- Billing, cost recovery or purchasing.
- Native mobile apps. The web app is responsive instead.
- Calendar sync (iCal/Google Calendar). Candidate for v2.
- Training or certification requirements before booking. The approval workflow covers this in v1.
- Recurring bookings for Students. Only staff can make them (§4.14).
- Bulk CSV import of users or equipment. Data is entered through the admin UI.
- Self-service "download my data" and "delete my account". Admins handle these requests by hand (§4.17).

### 1.3 Scale assumptions

- One department, with one or a few physical labs.
- About 10–200 equipment units across about 5–60 equipment types.
- Up to about 500 registered users, with up to about 50 using the app at once.

---

## 2. Users & Roles

| Role | Who | Summary |
|---|---|---|
| **Student** | Enrolled students | Browse equipment, make and cancel their own bookings. Subject to quotas and penalties. |
| **Teacher** | Faculty | Everything a student can do with higher limits. Approve or reject student requests for equipment that needs approval. Can override (bump) student bookings. |
| **Lab Tech** | Lab technicians | Check equipment out and in, mark no-shows, put units out of service, log maintenance, block time slots. Sees all bookings. |
| **Admin** | Department admin(s) | Manage users, roles, equipment, labs, rules and settings. View reports. Can do anything the other roles can. |

A user has exactly one role. Self-registration always creates a **Student**, and only an Admin can promote a user to Teacher, Lab Tech or Admin.

### 2.1 Permissions matrix

| Action | Student | Teacher | Lab Tech | Admin |
|---|:-:|:-:|:-:|:-:|
| Browse equipment & availability | ✅ | ✅ | ✅ | ✅ |
| Create / cancel own booking | ✅ | ✅ | ✅ | ✅ |
| Book on behalf of another user | – | – | ✅ | ✅ |
| Approve / reject booking requests | – | ✅ | – | ✅ |
| Override (bump) a student booking | – | ✅ | – | ✅ |
| View all bookings | – | ✅ | ✅ | ✅ |
| Check-out / check-in, mark no-show | – | – | ✅ | ✅ |
| Mark unit out of service / log maintenance | – | – | ✅ | ✅ |
| Create maintenance blocks | – | – | ✅ | ✅ |
| Manage equipment types & units | – | – | ✅ (units only) | ✅ |
| Manage users & roles | – | – | – | ✅ |
| Edit booking rules & settings | – | – | – | ✅ |
| Lift a no-show suspension | – | – | – | ✅ |
| View reports / export CSV | – | ✅ (read-only) | ✅ | ✅ |

---

## 3. Core Concepts

- **Lab:** A physical room with two sets of weekly opening hours: **term hours**, for example Mon–Fri 08:00–18:00, and **vacation hours**, for example Mon–Fri 09:00–13:00, or closed. Which set applies on a given date depends on the academic calendar (§4.16).
- **Term:** A named date range, for example "Odd Semester 2026", during which term hours apply. **Holidays** are dates when a lab is closed.
- **Accessory:** An item that goes out with a unit of an equipment type, for example "×10 probe (2)". It is checked when the unit goes out and when it comes back.
- **Equipment Type:** A kind of equipment, for example "Digital Oscilloscope DSO-X 2002A". It holds the description, photo, booking rules and the approval flag.
- **Equipment Unit:** One physical item of a type, with its own asset tag, for example `OSC-03`. Each unit belongs to a lab and has a status: `available`, `out_of_service` or `retired`.
- **Booking:** A reservation of one unit by one user over a time range.
- **Maintenance Block:** A time range when a unit can't be booked, for example a scheduled calibration.

### 3.1 Booking unit assignment

Users book an **equipment type** and a time range. The system assigns any free, in-service unit of that type, choosing the unit with the fewest bookings that day to spread wear. Users can optionally pick a **specific unit**, for example when it has particular accessories. The assigned unit is shown on the booking.

---

## 4. Functional Requirements

### 4.1 Authentication & accounts

- **FR-A1:** Users register with name, email and password. Admins can limit registration to allowed email domains, for example `@rajagiritech.edu.in`. Setting: `allowed_email_domains`.
- **FR-A2:** Users must verify their email with a link that expires after 24 h before they can book.
- **FR-A3:** Users can reset a forgotten password with a single-use email link that expires after 1 h.
- **FR-A4:** Passwords are at least 10 characters and hashed with Argon2id (or bcrypt with cost ≥ 12).
- **FR-A5:** After 5 failed logins in 15 minutes from the same account or IP, further attempts are temporarily blocked.
- **FR-A6:** Sessions are stored server-side in HTTP-only, Secure, SameSite=Lax cookies. They expire after 7 days idle or 30 days absolute.
- **FR-A7:** Admins can create users directly, which sends an invite email with a set-password link. Admins can also deactivate users. Deactivating a user cancels that user's future bookings.
- **FR-A8:** Users can edit their name and password. Changing their email requires re-verification.

### 4.2 Equipment catalogue

- **FR-E1:** The catalogue lists equipment types with photo, name, category, lab, short description, number of units in service and a "requires approval" badge.
- **FR-E2:** Users can search by name or category and filter by lab, by "available at time X–Y" and by "no approval needed".
- **FR-E3:** The type detail page shows the description, usage notes and safety notes (Markdown), booking rules, and a **day/week availability timeline** across all units.
- **FR-E4:** Admins create, edit and archive equipment types. Lab Techs and Admins add, edit and retire units: asset tag, serial number, lab, notes and status.

### 4.3 Making a booking

- **FR-B1:** The user picks an equipment type (and optionally a unit), a date, a start time and an end time.
  - Times snap to **15-minute increments**.
  - The booking must fall entirely within the lab's opening hours, on a single day.
- **FR-B2:** The user can add an optional purpose or notes field. The admin can make this field required for each equipment type.
- **FR-B3:** Before confirming, the system checks the booking against these rules. Any violation is shown with a clear message.
  1. The start time is in the future.
  2. The booking falls within the **advance booking window** (§4.6).
  3. The duration is no longer than the type's **maximum duration** (§4.6).
  4. The duration is at least the minimum of 15 min.
  5. The user isn't suspended (§4.8).
  6. The user is within their **quota** (§4.6).
  7. A unit is free: no overlapping active booking or maintenance block, and the unit is `available`.
  8. The user doesn't already have an overlapping booking for the **same equipment type**.
- **FR-B4:** The resulting status depends on the equipment type:
  - `requires_approval = false` → **Confirmed** immediately.
  - `requires_approval = true` and the booker is a Student → **Pending**. Teachers, Lab Techs and Admins are always confirmed immediately.
- **FR-B5:** A **pending booking holds the unit** so nobody else can take it while it waits for approval.
- **FR-B6:** The database must make double-booking impossible, even when two requests arrive at the same moment (§7.3).

### 4.4 Approval workflow

- **FR-P1:** Pending requests appear in an **Approvals** queue for all Teachers and Admins, oldest first, with the student's name, equipment, time, purpose and booking history (including no-show count).
- **FR-P2:** An approver can **Approve** or **Reject**. A rejection reason is required. The student is notified either way.
- **FR-P3:** A pending request that hasn't been decided by its start time, or within the `pending_expiry_hours` setting (default 48 h), whichever comes first, becomes **Expired**. Expiry frees the unit and notifies the student.
- **FR-P4:** Every decision records the approver and the time.

### 4.5 Changing & cancelling

- **FR-C1:** Users can cancel their own Pending or Confirmed booking at any time before it starts.
- **FR-C2:** A cancellation less than `late_cancel_minutes` before the start (default 60 min) counts as a **late cancellation**. It is recorded and shown in reports, but it does not count as a no-show.
- **FR-C3:** Users can edit the time of a booking before it starts. The edit is checked against all of the rules again. For equipment that needs approval, editing a confirmed booking sends it back to **Pending**.
- **FR-C4:** Lab Techs and Admins can cancel any booking, with a required reason. The owner is notified.

### 4.6 Booking rules (configurable)

Admins configure the department-wide defaults. Values on an equipment type override them for that type.

| Rule | Scope | Default |
|---|---|---|
| Max duration per booking | per type | 3 h |
| Advance booking window — Students | global, overridable per type | 14 days |
| Advance booking window — Teachers / Lab Tech / Admin | global | 60 days |
| Student quota: max active (upcoming) bookings | global | 3 |
| Student quota: max booked hours per ISO week | global | 8 h |
| Requires approval for students | per type | false |
| Purpose required | per type | false |
| Check-out grace period (no-show threshold) | global | 15 min |
| Late cancellation threshold | global | 60 min |
| Max waitlist entries per student | global | 3 |
| Waitlist claim window | global | 30 min |
| Max recurring series length (when no term is set) | global | 16 weeks |
| Data retention before anonymisation | global | 2 years |

Quotas apply **only to Students**. Pending and Confirmed bookings both count towards the quota.

### 4.7 Teacher override (bumping)

- **FR-O1:** If a Teacher or Admin tries to book a time when every unit of that type is taken, and at least one conflicting booking belongs to a **Student**, the system offers **"Override student booking"**.
- **FR-O2:** The teacher must enter a reason. The system chooses the student booking to bump: the one made most recently.
- **FR-O3:** The bumped booking moves to **Bumped** status, and the student gets an email and in-app notification. The notification includes the reason and a link to rebook, with alternative free slots suggested where possible.
- **FR-O4:** Booking cannot be overridden less than `override_min_notice_hours` before it starts (default 24 h). An override also cannot touch a booking that is already checked out.
- **FR-O5:** Teachers cannot bump other teachers' bookings.
- **FR-O6:** Every override is written to the audit log and shown in reports.

### 4.8 Check-in / check-out & no-shows

- **FR-K1:** The Lab Tech **Today** view lists the day's bookings by time, with these actions:
  - **Check out** records when the equipment was picked up, who handed it over, its condition (`good`, `minor issue` or `damaged`), optional notes and the **accessory checklist** (§4.15). This sets the booking to **Checked out**.
  - **Check in** records when it was returned, who received it, its condition, notes and the accessory checklist. This sets the booking to **Completed**. If the condition is `damaged`, the tech is asked to mark the unit out of service.
- **FR-K2:** If a confirmed booking is not checked out by *start + grace period* (default 15 min), a background job sets it to **No-show** and frees the rest of the slot. A Lab Tech can also mark a no-show manually, and can reverse a no-show within 24 h if it was marked by mistake.
- **FR-K3:** The no-show penalty applies to Students only. If a student gets `no_show_limit` no-shows (default 3) within a rolling `no_show_window_days` (default 30), they are **suspended** from new bookings for `suspension_days` (default 7). Their existing future bookings remain. The student is notified, and Admins can lift a suspension early.
- **FR-K4:** If equipment is still checked out 15 min after the booking's end time, it is flagged **Overdue** on the Lab Tech dashboard and both the user and the lab techs are notified. If the next booking for the same unit is affected, that booking's user is warned.

### 4.9 Maintenance & out-of-service

- **FR-M1:** Lab Techs and Admins can set a unit to **Out of service** with a reason. Future bookings on that unit are automatically **moved to another free unit** of the same type where possible. If none is free, the booking is cancelled and the user is notified.
- **FR-M2:** Lab Techs can create **maintenance blocks**: a unit or a whole type, a time range and a reason. Existing bookings that conflict are handled the same way as in FR-M1.
- **FR-M3:** Each unit has a **maintenance log** listing issue reports, out-of-service periods, repairs and calibrations, each with date, author and notes.
- **FR-M4:** Any user can **report an issue** from a booking they made, for example "probe 2 broken". The report creates a maintenance log entry and notifies the lab techs.

### 4.10 Notifications

Notifications are delivered **in-app**, through a bell with an unread count and a list, **and by email**. Users can turn off the non-critical emails, marked † below.

| Event | Recipient(s) |
|---|---|
| Booking confirmed | Booker |
| Booking request submitted (pending) | Booker; Teachers & Admins (in-app only) |
| Request approved / rejected / expired | Booker |
| Booking cancelled by staff | Booker |
| Booking bumped by teacher | Booker |
| Unit reassigned (maintenance) | Booker |
| Reminder: booking starts in 1 h † | Booker |
| Marked no-show | Booker |
| Suspension started / lifted | Student |
| Equipment overdue | Booker; Lab Techs |
| Issue reported / unit damaged | Lab Techs |
| Daily digest of pending approvals † | Teachers & Admins |
| Waitlist offer: a unit is free, claim it before a stated time | Waitlisted user |
| Waitlist offer expired or waitlist entry lapsed † | Waitlisted user |
| Recurring series created, with dates that were skipped | Booker |
| Booking cancelled because of a new holiday or closure | Booker |
| Accessories missing at check-in | Booker; Lab Techs |

Every email includes a deep link to the relevant booking.

### 4.11 Reports (Admin, Lab Tech; Teachers read-only)

- **FR-R1:** **Utilization:** booked hours and checked-out hours as a percentage of available hours, per type and per unit, over a chosen date range.
- **FR-R2:** **Popular equipment:** the top types by number of bookings and by hours, plus a heatmap of demand by hour of day and day of week.
- **FR-R3:** **No-shows & late cancellations:** totals over time and a table by user.
- **FR-R4:** **Approvals:** request counts, approval rate and median time to decision.
- **FR-R5:** **Maintenance:** downtime per unit and open issues.
- **FR-R6:** Every report table can be **exported to CSV**, and so can the raw booking list for a date range.

### 4.12 Audit log

Every state change to a booking, user role, unit status or setting is recorded with who made it, what changed, when it happened and the before/after values. Admins can view and filter the log.

### 4.13 Initial setup

- **FR-S1:** The first Admin account is created with a command:
  ```
  npm run create-admin -- --email <email> --name "<name>"
  ```
  The command asks for a password interactively, or sends a set-password email if `--invite` is passed. It creates the account already verified. If an Admin already exists, the command refuses to run unless `--force` is passed.
- **FR-S2:** A seed script (`npm run seed`) loads demo labs, equipment and users, for development and staging only. It refuses to run when `NODE_ENV=production`.
- **FR-S3:** After the first login, Home shows the first Admin a **setup checklist** until each step is done: add a lab and its hours, add a term, add equipment types and units, set the allowed email domains and review the booking rules.

### 4.14 Recurring bookings (staff only)

- **FR-RB1:** Teachers, Lab Techs and Admins can make a booking **repeat weekly**, for example every Tue 14:00–16:00. They can choose specific weekdays and an end, which is either a number of occurrences or an end date.
  - The series can't extend past the end of the current term, or `recurring_max_weeks` (default 16) if no term is set.
  - Staff advance booking windows don't apply to recurring series. The term end is the limit instead.
- **FR-RB2:** Before confirming, the user sees a **preview of every date**:
  - Each date is marked ✓ Free or ✗ Conflict, with the reason: booked, maintenance, holiday or closed.
  - Conflicting dates can be **skipped**, or the user can choose "Override student booking" for them, following the §4.7 rules.
  - Free dates are booked and conflicting dates are not.
- **FR-RB3:** Each occurrence is a normal Booking linked to a `RecurrenceSeries`. Unit assignment tries to give the **same unit** every week, and uses another unit for a date when that one isn't free.
- **FR-RB4:** The user can cancel or edit **this occurrence**, **this and following** occurrences, or **the whole series**. Past occurrences are never changed.
- **FR-RB5:** Staff bookings, including recurring ones, are confirmed immediately even for approval-required equipment (§4.3).
- **FR-RB6:** Recurring series are shown with a repeat icon (Lucide `Repeat`) in all booking lists and on the calendar.

### 4.15 Accessories checklist

- **FR-X1:** Admins define each equipment type's **accessory list**. Each item has a name and an expected quantity, for example "Probe ×2", "BNC cable ×1" or "Power cord ×1".
- **FR-X2:** At **check-out**, the Lab Tech sees the checklist with every item ticked by default. The tech unticks or adjusts the quantity of anything not handed over, and that is recorded.
- **FR-X3:** At **check-in**, the checklist shows what went out. Items not ticked back in are recorded as **missing**:
  - The booking gets an **"Accessories missing"** flag.
  - A maintenance log entry of kind `ISSUE` is created on the unit.
  - The booker and the Lab Techs are notified.
- **FR-X4:** A Lab Tech resolves a missing-accessory flag once the item is returned or replaced. Open flags appear on the Lab Tech dashboard and in the maintenance report.
- **FR-X5:** The equipment detail page lists the type's accessories, so users know what they will receive.

### 4.16 Academic calendar: terms & holidays

- **FR-T1:** Admins manage **Terms**: a name, a start date and an end date. Terms can't overlap.
- **FR-T2:** On dates inside a term, the lab's **term hours** apply. Outside every term, its **vacation hours** apply. Vacation hours can be "closed" on every day.
- **FR-T3:** Admins manage **Holidays and closures**. Each has a date or date range and a reason, and applies to every lab or one lab. On those dates nothing can be booked, and the timeline shows "Closed — <reason>".
- **FR-T4:** If a new holiday, a term change or a change to opening hours makes existing future bookings invalid, the admin first sees **how many bookings are affected** and must confirm. Those bookings are then cancelled with the reason, and the bookers are notified.
- **FR-T5:** Quota weeks, the advance booking window and recurring series all use these rules.

### 4.17 Privacy & data retention

- **FR-D1:** **Privacy notice at sign-up.** The register form has a required checkbox: "I have read the Privacy Notice", linking to `/privacy`.
  - The notice is Markdown that Admins can edit. It explains what data is collected (name, email, booking history and no-shows), why, who can see it, how long it is kept and who to contact.
  - The app stores `consentAt` and `consentVersion` for each user.
  - When an Admin publishes a new version, users must accept it at their next login before they can continue.
- **FR-D2:** **Retention.** A monthly job **anonymises** data older than `retention_years` (default 2):
  - In bookings, check events and no-show records, the user link is replaced with an anonymised placeholder, and purpose and notes text are cleared. The user's role at booking time, the equipment, times and status are kept, so the statistics stay correct.
  - Notifications older than 90 days are deleted.
  - In audit log entries older than 2 years, the actor is replaced with "Anonymised user".
  - Deactivated accounts with no activity for 2 years are anonymised: the name becomes "Former user", the email becomes a hashed placeholder and the password hash is removed.
- **FR-D3:** **Data requests by hand.** A user who wants a copy of their data, or wants their account deleted, contacts the Admin, whose address is given in the privacy notice. An Admin can **export one user's data** (profile and bookings as CSV) and **anonymise a user now** from the Users page. Anonymising cancels the user's future bookings, and the action is audit-logged.
- **FR-D4:** Reports never show anonymised users' names. Anonymised users appear in aggregates only.

### 4.18 Waitlist

- **FR-W1:** If no unit of a type is free for the requested time, the booking form offers **"Join waitlist"** in place of Book. The request must otherwise pass every rule: window, duration, suspension and so on. Students may hold at most `waitlist_max_entries` (default 3) waitlist entries at a time. Waitlist entries **don't count** towards booking quotas.
- **FR-W2:** When a unit becomes free for the **whole** requested time, through a cancellation, rejection, expiry, maintenance ending or a unit returning to service, the **first person in line** whose request now fits receives an **offer**:
  - The unit is held for that person with an `OFFERED` booking, which blocks the slot.
  - The person is notified by email and in-app, with a **Claim** button.
- **FR-W3:** The offer lasts `waitlist_claim_minutes` (default 30), or until 15 min before the start, whichever comes first.
  - **Claiming** re-checks the rules. The booking becomes Confirmed, or Pending if a student is booking approval-required equipment.
  - If the person **declines** or the offer **times out**, the hold is released and the next person in line gets an offer.
- **FR-W4:** Waitlist entries **lapse** automatically when the requested start time passes, and the user is notified. Users can leave a waitlist at any time from My Bookings, where entries appear under a **Waitlist** tab.
- **FR-W5:** No offers are made less than 15 min before the start. Within that time, a freed slot simply becomes bookable by anyone.

---

## 5. Booking State Machine

```
                ┌──────────── reject ───────────► Rejected
                │
 create ──► Pending ── approve ──► Confirmed ── check-out ──► CheckedOut ── check-in ──► Completed
   │            │                    │   │                        │
   │            └─ expire ─► Expired │   └─ grace elapsed ─► NoShow └─ (past end) flagged Overdue
   │                                 │
   └── (no approval needed) ─────────┘
 Pending/Confirmed ── user/staff cancel ──► Cancelled
 Pending/Confirmed ── teacher override ──► Bumped

 (waitlist) unit frees ──► Offered ── claim ──► Confirmed / Pending
                              └── decline / timeout ──► Lapsed (next in line gets an offer)
```

The **active** statuses are `Offered`, `Pending`, `Confirmed` and `CheckedOut`. These are the statuses that block a unit's time slot.

---

## 6. UI Specification

### 6.1 Design principles

- **Clean and minimal.** Use plenty of white space, neutral greys and a single teal accent. Colour is reserved for meaning, such as actions and statuses, not decoration.
- **Booking first.** From any dashboard, a user can start a booking in no more than 2 taps or clicks.
- **Mobile-first and responsive.** Layouts are designed at 360 px wide first and scale up to wide desktops.
- **Show availability, don't describe it.** Free and busy time is always shown on a visual timeline, not only in text.
- **Accessible by default.** WCAG 2.1 AA in both light and dark themes.

### 6.2 Visual design

**Themes:** light and dark. The app follows the OS setting (`prefers-color-scheme`) by default, and a manual **Light / Dark / System** toggle in the user menu overrides it. The choice is saved per user on the device. Theme tokens are CSS variables that shadcn/ui and Tailwind both read.

**Colour tokens**

| Token | Light | Dark | Use |
|---|---|---|---|
| `--background` | `#FFFFFF` | `#020617` (slate-950) | Page background |
| `--surface` | `#F8FAFC` (slate-50) | `#0F172A` (slate-900) | Cards, sidebar, panels |
| `--border` | `#E2E8F0` (slate-200) | `#1E293B` (slate-800) | Dividers, inputs |
| `--foreground` | `#0F172A` (slate-900) | `#F8FAFC` (slate-50) | Body text |
| `--muted-foreground` | `#64748B` (slate-500) | `#94A3B8` (slate-400) | Secondary text |
| `--primary` | `#0D9488` (teal-600) | `#2DD4BF` (teal-400) | Primary buttons, links, selection, focus ring |
| `--primary-hover` | `#0F766E` (teal-700) | `#5EEAD4` (teal-300) | Hover/active |
| `--primary-foreground` | `#FFFFFF` | `#042F2E` (teal-950) | Text on primary |
| `--primary-subtle` | `#F0FDFA` (teal-50) | `#134E4A` (teal-900) | Selected rows, timeline selection fill |
| `--destructive` | `#DC2626` (red-600) | `#F87171` (red-400) | Destructive actions, errors |

**Typography**
- Font: **Inter**, using the variable font through `next/font`, with a system-ui fallback.
- Type scale:

  | Style | Size / line height | Weight |
  |---|---|---|
  | Page title | 24/32 | semibold |
  | Section title | 18/28 | semibold |
  | Body | 14/20 | regular |
  | Small / meta | 12/16 | regular |

- Times and asset tags use tabular numerals (`font-variant-numeric: tabular-nums`).

**Shape and spacing**
- Spacing follows a 4 px grid.
- Corner radii: 8 px for inputs and buttons, 12 px for cards, full pills for badges.
- Shadows are subtle and used only on popovers and dialogs. Cards use borders, not shadows.

**Icons:** Lucide (`lucide-react`) at 16 px inline and 20 px in navigation, with 1.5–2 px strokes.

### 6.3 Layout & navigation

- **Desktop (≥ 1024 px):** a fixed **left sidebar** 240 px wide, which can collapse to a 64 px icon rail (the choice is remembered). The sidebar holds:
  - the LabBook logo,
  - the navigation items for the user's role,
  - a prominent **"+ New booking"** button at the top,
  - the user menu at the bottom (profile, theme toggle, sign out).
- **Tablet and mobile (< 1024 px):** the sidebar becomes a **slide-in drawer**, opened by a **hamburger** button in a slim top bar. The top bar shows the page title, the notification bell and the avatar. The drawer closes when the user picks a page, taps outside it or presses Esc. On booking-related screens, a floating **"+ Book" button** (FAB) appears at the bottom right.
- **Content area:** maximum width 1280 px, centred, with 16 px side gutters on mobile and 32 px on desktop. Each page has a header with the title, breadcrumbs where relevant and primary actions on the right.
- **Navigation by role:**

  | Role | Navigation items |
  |---|---|
  | Student | Home, Equipment, Calendar, My Bookings, Notifications |
  | Teacher | Student items + Approvals (with pending count badge), Reports |
  | Lab Tech | Home, **Today**, Equipment, Calendar, Units & Maintenance, My Bookings, Reports, Notifications |
  | Admin | All of the above + an **Admin** group: Users, Equipment types, Labs, Settings, Audit log |

### 6.4 Home: role-based dashboard

After logging in, each user lands on **Home**, which shows widgets for their role:

| Role | Widgets |
|---|---|
| **Student** | Greeting; **Next booking** card (equipment, unit, time, countdown, "Get directions to lab" and cancel); **Quick book** (search box + recently used equipment); **Upcoming bookings** (next 5); **Pending requests**; quota meter ("2 of 3 active bookings · 5 h of 8 h this week"); a suspension banner if suspended |
| **Teacher** | **Approvals waiting** (count and the top 5 with inline Approve/Reject); Next booking; Quick book; Upcoming bookings |
| **Lab Tech** | **Today at a glance**: counts of due check-outs, due returns, overdue items and units out of service; a link to the full Today desk; open issue reports |
| **Admin** | Key stats for this week (bookings, use %, no-shows, pending approvals); open issues; recent audit events; shortcuts to Users, Equipment and Settings |

On mobile, widgets stack in one column. On desktop they sit in a responsive 2–3 column grid.

### 6.5 Screen inventory


| Screen | Roles | Notes |
|---|---|---|
| Login / Register / Verify / Reset | All | Register includes the privacy-notice checkbox (§4.17) |
| **Privacy notice** (`/privacy`) | Public | Admin-editable Markdown. A re-consent screen appears when the version changes. |
| **Catalogue** | All | Search, filters, cards |
| **Equipment detail** | All | Info, rules, availability timeline, "Book" button |
| **Booking form** | All | Date and time pickers, unit picker (optional), purpose, live rule validation, override prompt for teachers |
| **My Bookings** | All | Tabs for Upcoming, Pending and Past. Cancel, edit, report issue. |
| **Calendar** | All | Day and week view of all bookings. Staff see names; students see only "Booked". |
| **Approvals** | Teacher, Admin | Queue with approve and reject buttons, filters |
| **Today (Lab desk)** | Lab Tech, Admin | Check-out/check-in list, overdue and no-show flags. Big tap targets for tablet use. |
| **Units & Maintenance** | Lab Tech, Admin | Unit list, status toggle, maintenance log, maintenance blocks |
| **Reports** | Admin, Lab Tech, Teacher | Charts and CSV export |
| **Admin: Users** | Admin | Invite, change role, deactivate, lift suspension |
| **Admin: Equipment types, Labs, Settings** | Admin | Catalogue, accessory lists, term and vacation hours, rule defaults, allowed email domains, privacy notice editor |
| **Admin: Academic calendar** | Admin | A year view with terms as coloured bands and holidays marked. Add or edit a term or holiday, with an "affected bookings" preview before saving. |
| **Admin: Audit log** | Admin | |
| **Notifications** | All | List with mark-as-read |
| **Profile** | All | Name, password, email preferences, theme |

### 6.6 Equipment catalogue

- **Card grid with photos:**

  | Screen width | Columns |
  |---|---|
  | < 640 px | 1 |
  | 640–1023 px | 2 |
  | 1024–1279 px | 3 |
  | ≥ 1280 px | 4 |

- **Card contents:** a 4:3 photo (with a category icon on a teal-subtle background as a fallback), name, category and lab, and an availability line such as "**3 of 5 free now**" (green dot) or "None free now · next at 14:30" (grey dot). Badges show "Approval needed" (lock icon) and "Out of service" where they apply. A **Book** button opens the booking flow pre-filled.
- **Filters:**
  - Desktop: a filter bar above the grid with search, category, lab, "Free between [time]–[time]" and a "No approval needed" toggle.
  - Mobile: a "Filters" button opens a bottom sheet. Active filters show as removable chips.
- **Empty and loading states:** skeleton cards while loading, and an illustrated empty state with a "Clear filters" button when nothing matches.

### 6.7 Booking flow: drag-on-timeline picker

This is the main interaction in the app. It appears on the equipment detail page and in the **New booking** sheet.

**Desktop layout**
- A horizontal **day timeline**: hours run left to right across the lab's opening hours, with gridlines every 15 min and labels every hour.
- A **summary row** at the top shows availability across the whole equipment type. Each 15-min cell is shaded by how many units are free: full teal-subtle when all are free, lighter as fewer are free, and hatched grey when none are free.
- Below the summary row, **one row per unit**, labelled with its asset tag, can be expanded with "Show units".
- A date selector with ‹ › buttons and a "Today" button sits above the timeline.

**Mobile layout**
- A **vertical day column**, with time running from top to bottom, so it scrolls naturally.
- Unit rows are hidden. The summary column is shown, and a "Choose specific unit" dropdown is available.

**Selecting a time range**
- **Mouse:** click and drag across free time to select a range. The selection snaps to 15 min and appears as a teal block with a label such as "10:00–12:00 · 2 h". The block's edge handles can be dragged to resize it.
- **Touch:** tap a free cell to create a 1 h selection, or a selection of the maximum duration if that is shorter. Drag the top and bottom handles to resize. A long-press followed by a drag moves the block. Ordinary swipes scroll the page and never create a selection by accident.
- **Keyboard:** the timeline is focusable. Use ←/→ (↑/↓ on mobile) to move the selection by 15 min, Shift + arrows to resize it, and Enter to confirm.
- **Form fields stay in sync:** date, start and end fields are always visible below the timeline (or in the bottom sheet on mobile). Editing them moves the selection, and dragging updates them. These fields are the accessible fallback.

**What blocks look like**

| Block | Appearance |
|---|---|
| Other people's bookings | Solid grey labelled "Booked". Staff also see the booker's name on hover or tap. |
| The user's own bookings | Teal outline |
| Maintenance blocks | Diagonal stripes with a wrench icon |
| Closed hours | Dimmed, and the lab's holidays are marked closed |
| Current time | A red "now" line |

**Live validation**
- The rules engine runs on the client as the user drags.
- If the selection breaks a rule, for example by going over the maximum duration, crossing busy time or going beyond the booking window, the block turns red. An inline message explains why, such as "Max 3 h for this equipment", and the **Book** button is disabled.
- For Teachers, a selection that covers student bookings shows an amber **"Override student booking"** option instead (§4.7). Choosing it opens a dialog that requires a reason.

**Summary and confirmation**
- A summary sits in a sticky bottom bar on mobile and a side panel on desktop. It shows the equipment, the assigned unit (or "Any available unit"), the time, the duration, a purpose field when required and the approval notice ("A teacher must approve this request").
- The primary button reads **"Book"**, or **"Request booking"** when approval is needed.
- **When nothing is free:** if the selection covers time when no unit is free, the button becomes **"Join waitlist"** (secondary style). It shows the user's place in line, for example "You'll be #2", and suggests the nearest free times as chips.
- **Repeat weekly (staff only):** a "Repeat" toggle in the summary shows weekday chips and an end setting (number of times, or "until end of term"). Turning it on opens a **preview list of every date**, each marked ✓ or ✗ with its reason, with a skip or override choice for each conflict. The button then reads **"Book N dates"**.
- After a successful booking, a success toast appears with **View booking** and **Add another** links, and the user moves to the booking detail page.

### 6.8 Status badges

Statuses appear as pill badges with an **icon, text and colour**, so meaning never depends on colour alone. Badges use a subtle tinted background with strong text, and both themes meet a contrast ratio of at least 4.5:1.

| Status | Colour | Lucide icon |
|---|---|---|
| Pending | Amber | `Clock` |
| Confirmed | Green | `CircleCheck` |
| Checked out | Blue | `PackageOpen` |
| Completed | Slate | `CheckCheck` |
| Cancelled | Slate | `CircleX` |
| Rejected | Red | `Ban` |
| Expired | Slate | `Hourglass` |
| No-show | Red | `UserX` |
| Bumped | Violet | `ArrowLeftRight` |
| Offered (waitlist) | Teal | `BellRing` |
| Waitlisted / Lapsed | Slate | `ListOrdered` / `Hourglass` |
| Accessories missing (flag) | Amber, solid | `PackageX` |
| Overdue (flag) | Red, solid | `TriangleAlert` |
| Unit: Available / Out of service / Retired | Green / Red / Slate | `Circle` / `Wrench` / `Archive` |

The same colours are used for booking blocks in the calendar views.

### 6.9 Other key screens

- **Calendar:** Day and Week views; Week is the default on desktop and Day on mobile. Users can filter by lab and equipment type. Booking blocks use the status colours, and clicking a block opens the booking detail in a side sheet.
- **My Bookings:** tabs for Upcoming, Pending and Past. On mobile, each booking is a card. On desktop it's a table with time, equipment, unit and status. Row actions (Edit, Cancel, Report issue) sit in a "⋯" menu.
- **Approvals:** a list of request cards. Each shows the student's avatar and name, their no-show count badge, the equipment, the time and the purpose, with **Approve** (primary) and **Reject** (outline) buttons. Rejecting opens a dialog that requires a reason. On desktop, checkboxes allow approving several requests at once.
- **Today (Lab desk):** built for a tablet at the lab counter.
  - Large rows with tap targets of at least 48 px, grouped into **Due to check out**, **Checked out**, **Overdue** (pinned to the top in red) and **Done**.
  - **Check out** and **Check in** open a short sheet with three large condition buttons (Good, Minor issue, Damaged), the **accessory checklist** (large checkboxes with quantity steppers) and a notes field. At check-in, any item not ticked is highlighted in amber, and the sheet asks "Mark as missing?" before saving.
  - The page refreshes automatically every 60 s.
- **Units & Maintenance:** a table of units with status toggles. Each unit has a detail page with a maintenance log timeline and a "Block time" action.
- **Reports:** a date-range picker and charts in cards with CSV buttons. Charts use the teal palette in both themes, following the dataviz guidance.
- **Admin pages:** tables with search, sorting and pagination (25 rows per page). Create and edit happen in side sheets on desktop and full-screen sheets on mobile.

### 6.10 Feedback, states & components

- **Components:** shadcn/ui (Radix-based) for buttons, inputs, dialogs, sheets, dropdowns, tabs, tooltips, toasts (Sonner) and data tables (TanStack Table).
- **Toasts:** shown bottom-right on desktop and at the top on mobile. They auto-dismiss after 5 s, and error toasts stay until dismissed. Destructive actions such as cancelling, rejecting or retiring a unit always ask for confirmation in a dialog first. Booking cancellation also offers **Undo** for 5 s, as long as the booking has not started.
- **Loading:** skeletons for content. Buttons show inline spinners and are disabled while busy. Optimistic updates are used for marking notifications as read.
- **Empty states:** a friendly line plus a primary action, for example "No upcoming bookings. **Book equipment**".
- **Errors:** form errors appear inline under each field. Server and rule errors appear in an alert at the top of the form. Custom 404 and 500 pages each include a "Go home" link.
- **Notifications bell:** a popover (a full page on mobile) showing the latest 10 notifications, an unread dot, "Mark all read" and "View all".
- **Dates and times:** 24-hour format (`14:30`) and dates such as "Fri, 13 Nov". Relative times ("in 2 h", "5 min ago") appear where helpful, with the absolute time in a tooltip.

### 6.11 Responsive breakpoints & accessibility

| Breakpoint | Width | Layout |
|---|---|---|
| `sm` | ≥ 640 px | 2-column grids |
| `md` | ≥ 768 px | Tables replace cards in lists |
| `lg` | ≥ 1024 px | Fixed sidebar, horizontal timeline |
| `xl` | ≥ 1280 px | 4-column catalogue |

- No horizontal page scrolling at 360 px. The timeline scrolls on its own if it's wider than the screen.
- Touch targets are at least 44 × 44 px, and at least 48 px on the Lab desk.
- Focus rings are visible (2 px, in `--primary`), and every page has a skip-to-content link.
- Dialogs and drawers trap focus and close with Esc. The timeline has ARIA labels, for example "Unit OSC-03, 10:00 to 10:15, free".
- Animation respects `prefers-reduced-motion`: drawer and sheet transitions take 150–200 ms and are turned off under reduced motion.

---

## 7. Technical Design

### 7.1 Stack

- **Framework:** Next.js (App Router) with TypeScript, using React Server Components and Server Actions for mutations.
- **Database:** PostgreSQL 15+.
- **ORM:** Prisma, with raw SQL migrations for the exclusion constraints in §7.3.
- **Auth:** Better Auth (or an equivalent library) with email/password, email verification, password reset and DB-backed sessions.
- **UI:** Tailwind CSS, shadcn/ui, `next-themes` for light/dark mode, `lucide-react` icons and Inter through `next/font`. The drag-to-select booking timeline (§6.7) is a custom component built on Pointer Events. FullCalendar or a custom grid is used for the Calendar Day and Week views.
- **Validation:** Zod schemas shared by the client and the server.
- **Email:** Nodemailer over SMTP (the institution's SMTP server or a provider), with React Email templates.
- **Background jobs:** pg-boss, a Postgres-backed queue, or a cron-triggered route handler that runs every minute. It handles reminders, no-show detection, pending expiry, overdue detection and the daily digest.
- **Charts:** Recharts.
- **Testing:** Vitest for unit tests of the rules engine, and Playwright for end-to-end tests of the main flows.
- **Deployment:** A Docker image running the Next.js app, Postgres and the worker. It can be hosted on a department server or any Node host. The scale is small enough for a single VM.

### 7.2 Data model (Prisma-style outline)

```text
User            id, name, email (unique, citext), passwordHash?, role (STUDENT|TEACHER|LAB_TECH|ADMIN),
                emailVerifiedAt, isActive, suspendedUntil, emailPrefs (json),
                consentAt, consentVersion, lastActiveAt, anonymisedAt?, createdAt
Session / VerificationToken / PasswordResetToken   (managed by auth library)
PrivacyNotice   version (pk), bodyMd, publishedAt, publishedById

Lab             id, name, location, timezone,
                termHours (json: {mon:[["08:00","18:00"]], ...}), vacationHours (json, may be empty)
Term            id, name, startDate, endDate                -- non-overlapping
Holiday         id, labId?, startDate, endDate, reason      -- closure; labId null = all labs

EquipmentType   id, name, category, description(md), safetyNotes(md), photoUrl,
                requiresApproval bool, purposeRequired bool,
                maxDurationMin int?, studentAdvanceDays int?, isArchived, createdAt
Accessory       id, typeId, name, expectedQty, sortOrder, isArchived
EquipmentUnit   id, typeId, labId, assetTag (unique), serialNo, notes,
                status (AVAILABLE|OUT_OF_SERVICE|RETIRED), createdAt

Booking         id, unitId, typeId, userId, createdById,
                startsAt timestamptz, endsAt timestamptz,
                status (OFFERED|PENDING|CONFIRMED|CHECKED_OUT|COMPLETED|CANCELLED|REJECTED|EXPIRED|NO_SHOW|BUMPED|LAPSED),
                userRoleAtBooking, purpose, decidedById?, decidedAt?, decisionReason?,
                cancelledById?, cancelledAt?, cancelReason?, isLateCancel bool,
                bumpedByBookingId?, seriesId?, waitlistEntryId?, offerExpiresAt?,
                accessoriesMissing bool, createdAt, updatedAt
                -- userId nullable after anonymisation
RecurrenceSeries id, createdById, typeId, preferredUnitId?, weekdays int[], startTime, endTime,
                 startDate, endDate, createdAt
WaitlistEntry   id, userId, typeId, unitId?, startsAt, endsAt, purpose,
                status (WAITING|OFFERED|CLAIMED|LAPSED|LEFT), createdAt   -- queue order = createdAt
CheckEvent      id, bookingId, kind (OUT|IN), at, staffId, condition (GOOD|MINOR|DAMAGED), notes
CheckAccessory  id, checkEventId, accessoryId, qty, isMissing bool, resolvedAt?, resolvedById?

MaintenanceBlock id, unitId, startsAt, endsAt, reason, createdById
MaintenanceLog   id, unitId, kind (ISSUE|OUT_OF_SERVICE|REPAIR|CALIBRATION|BACK_IN_SERVICE),
                 notes, reportedById, bookingId?, resolvedAt?, createdAt

NoShowRecord    id, userId, bookingId, createdAt, reversedAt?
Notification    id, userId, type, payload json, readAt?, createdAt
Setting         key (pk), value json          -- global rule defaults
AuditLog        id, actorId, entity, entityId, action, before json, after json, createdAt
```

**Indexes:** `Booking(unitId, startsAt)`, `Booking(userId, startsAt)`, `Booking(status, startsAt)`, `Booking(seriesId)`, `WaitlistEntry(typeId, status, startsAt, createdAt)` and `Notification(userId, readAt)`.

### 7.3 Preventing double-booking

These are enforced in the database so they hold even under concurrent requests:

```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;

ALTER TABLE "Booking" ADD CONSTRAINT booking_no_overlap
  EXCLUDE USING gist ("unitId" WITH =, tstzrange("startsAt","endsAt",'[)') WITH &&)
  WHERE (status IN ('OFFERED','PENDING','CONFIRMED','CHECKED_OUT'));

ALTER TABLE "MaintenanceBlock" ADD CONSTRAINT mblock_no_overlap
  EXCLUDE USING gist ("unitId" WITH =, tstzrange("startsAt","endsAt",'[)') WITH &&);
```

Bookings that conflict with maintenance blocks are checked inside the same transaction, with `SELECT ... FOR UPDATE` on the unit row. Unit assignment runs as follows: inside a transaction, try candidate units in order and insert. If an insert hits an exclusion violation, try the next unit. If none succeeds, return "No units available".

### 7.4 Rules engine

All of the booking rules (§4.3–4.8) live in one pure, unit-tested module, `lib/rules.ts`. It takes the user, the type, the time range, the settings and the user's existing bookings, and returns either `ok` or a list of typed violations. The UI uses the same module for live feedback and the server uses it for enforcement. The server is authoritative.

### 7.5 Time handling

- Timestamps are stored in UTC (`timestamptz`).
- Times are displayed and validated in the lab's timezone (default `Asia/Kolkata`).
- Quota weeks are ISO weeks in the lab's timezone.

### 7.6 Background jobs (every minute unless noted)

| Job | Action |
|---|---|
| `expirePending` | Pending bookings past their expiry time → Expired, and notify the booker |
| `detectNoShows` | Confirmed bookings with `startsAt + grace < now` → NoShow; apply suspensions |
| `detectOverdue` | CheckedOut bookings with `endsAt + 15min < now` → notify |
| `sendReminders` | Bookings starting in 60 min that haven't been reminded yet → send reminder |
| `dailyDigest` (07:00) | Pending-approval summary to Teachers & Admins |
| `processWaitlist` | Expire timed-out offers (→ Lapsed), make the next offer for each freed slot, lapse entries whose start has passed. Also runs immediately whenever a slot frees up. |
| `anonymise` (monthly, 1st at 02:00) | Data retention rules (§4.17 FR-D2) |

Every job is idempotent, so running it twice has no extra effect.

### 7.7 API surface

Mutations use Server Actions. A small set of REST route handlers covers CSV export and the job trigger.

- `GET /api/export/bookings.csv?from&to`
- `GET /api/export/report/:name.csv?from&to`
- `POST /api/jobs/tick`, protected by a secret header and called by cron if pg-boss isn't used.

Every action checks the user's role on the server and checks that the user owns the resource, according to §2.1.

---

## 8. Non-Functional Requirements

- **Performance:** Catalogue and calendar pages load in under 1.5 s on 4G (p95). Booking submission completes in under 500 ms on the server (p95).
- **Availability:** Best effort on a single VM. Nightly `pg_dump` backups are kept for 30 days.
- **Security:**
  - OWASP Top 10 practices.
  - CSRF protection, built into Server Actions.
  - Rate limiting on auth endpoints.
  - Role checks on every server action.
  - No PII in logs.
  - HTTPS only.
- **Privacy:** Students see only their own booking details. Other people's bookings appear to them as anonymous "Booked" blocks. Staff see names.
- **Accessibility:** WCAG 2.1 AA, including full keyboard navigation of the booking form and calendar, and not relying on colour alone for booking status.
- **Browser support:** Latest two versions of Chrome, Edge, Firefox and Safari, on desktop and mobile.

---

## 9. Acceptance Criteria (key scenarios)

1. Two students submit bookings for the last free oscilloscope unit at the same moment. Exactly one succeeds, and the other sees "No units available".
2. A student books an approval-required type. The booking is Pending and the unit is held. A teacher approves it. The student gets an email and an in-app notification, and the status is Confirmed.
3. A student with 3 upcoming bookings tries a 4th and is blocked with a quota message.
4. A student tries to book 15 days ahead (window is 14) and is blocked.
5. A student tries a 4-hour booking where the maximum is 3 h, and is blocked.
6. A confirmed booking isn't checked out by start + 15 min, so it becomes NoShow. After 3 no-shows in 30 days the student is suspended for 7 days and can't create bookings.
7. A teacher books a time when all units are held by students more than 24 h away. The teacher overrides with a reason. The most recently made student booking is Bumped and that student is notified.
8. A lab tech marks a unit out of service. Its future bookings move to another free unit, or are cancelled with notification when none is free.
9. A lab tech checks in a unit as "damaged" and is asked to put it out of service. A maintenance log entry is created.
10. An admin exports bookings for a month as CSV, and the row count matches the bookings list.
11. A fresh install runs `npm run create-admin`, after which that admin can log in and sees the setup checklist. Running the command a second time without `--force` is refused.
12. A teacher creates a weekly series for 8 weeks. One week falls on a holiday and one conflicts with a booking. The preview shows 6 ✓ and 2 ✗. The teacher skips both conflicts, 6 bookings are created on the same unit, and cancelling "this and following" from week 4 cancels weeks 4–8 only.
13. A student can't book a time when every unit is taken, so they join the waitlist. Another user cancels, and the student receives an offer with the unit held. They don't claim it within 30 min, so the offer lapses and the next person in line gets an offer.
14. A lab tech checks in a unit with 1 of 2 probes missing. The booking is flagged, a maintenance log entry is created, and the booker and the lab techs are notified. When the tech later resolves the flag, it disappears from the dashboard.
15. An admin adds a holiday that conflicts with 4 bookings. A confirmation shows "4 bookings will be cancelled". After the admin confirms, all 4 bookers are notified, and that date shows "Closed" on the timeline.
16. Registering without ticking the privacy-notice checkbox is blocked. After an admin publishes a new notice version, an existing user must accept it at their next login.
17. Bookings older than 2 years show "Anonymised user" after the monthly job runs, and the utilisation report totals are unchanged.

---

## 10. Milestones (suggested)

1. **Foundation:** project setup, theme tokens and app shell (sidebar, drawer, light/dark mode), auth (register, verify, reset, privacy consent), `create-admin` command, seed script, roles, labs (term and vacation hours), academic calendar, equipment types, units and accessories admin.
2. **Booking core:** catalogue, availability timeline, booking form, rules engine (including terms and holidays), exclusion constraints, My Bookings, cancel and edit.
3. **Workflow:** approvals, teacher override, recurring series, waitlist and offers, notifications (in-app and email), reminders.
4. **Lab desk:** check-out and check-in with the accessory checklist, no-show job, suspensions, overdue, missing-accessory flags, maintenance and out-of-service handling.
5. **Insights & compliance:** reports, CSV export, audit log viewer, retention and anonymisation job, export or anonymise a single user.
6. **Hardening:** accessibility pass, end-to-end tests, Docker deployment, backups.

---

## 11. Open Questions

- Should training or certification be required before booking hazardous equipment in v2?
- Should Admins get bulk CSV import for users and equipment in v2?
- Should self-service data export and account deletion be added later, in case DPDP compliance needs it?
- Should students see *who* booked equipment, for example to coordinate with lab partners? The current default is no.
- Should the no-show penalty also apply to teachers, perhaps as a warning only?
- Is a kiosk or QR-code check-out (scan the unit's asset tag) wanted for v2?
- Is calendar sync (an iCal feed for each user) wanted for v2?
